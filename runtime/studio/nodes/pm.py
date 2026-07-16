"""
Node LangGraph - Agent PM.

Seul agent stateful du studio (mémoire portée par le checkpointer SQLite
LangGraph, voir ADR 0001 et ADR 0003). Ce node couvre deux activations
distinctes du même agent, distinguées par state.current_phase :

- Phase.RECEPTION / Phase.CADRAGE (phases 0-1, modèle models.pm_opus) :
  dialogue de raffinement itératif avec l'utilisateur jusqu'à validation
  de la fiche racine (voir docs/workflow.md phase 1, checklist d'intention,
  ADR 0008). Si state.imported_brief_content est renseigné (raccourci
  "import de brief existant", voir cli.py::_run_async), ce dialogue est
  remplacé par une revue directe du document importé (_run_brief_import) :
  le document devient architect_brief_path tel quel et la phase 2 (audit
  amont Architecte) est sautée — décision actée avec l'utilisateur, voir
  docs/roadmap.md.
- Phase.FICHES (phase 3, modèle models.pm_sonnet) : définition de la
  séquence d'agents et écriture d'une fiche par agent, puis création de
  la branche du run (premier commit-point, voir ADR 0007).

Le node ne couvre pas la phase 10 (clôture) : celle-ci est gérée par
studio.nodes.closer, Python pur sans appel LLM.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console

from studio.config import StudioConfig
from studio.metrics import record_agent_result
from studio.routing import agent_iteration_count, max_iterations_exceeded, should_checkpoint
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.claude_code import run_claude_code
from studio.tools.filesystem import (
    FEEDBACK_HEADING,
    PM_FICHES_SCHEMA,
    parse_agent_file_blocks,
    parse_pm_structured_output,
    read_card,
    write_card,
)
from studio.tools.git import commit_as_agent, create_run_branch
from studio.tools.tracer import AgentTracer, RunTracer

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _DEVAIMAZING_ROOT / "prompts" / "pm.md"
_CARD_ROOT_IMPORT_TEMPLATE_PATH = _DEVAIMAZING_ROOT / "templates" / "card-root-import.md.template"

_QUESTION_PATTERN = re.compile(r"QUESTION:\s*(.+)", re.DOTALL)
_FICHE_VALIDEE_PATTERN = re.compile(r"FICHE_VALIDEE:\s*\n(.*)", re.DOTALL)
_FEATURE_NAME_PATTERN = re.compile(r"\*\*Nom de la feature\*\*\s*:\s*(.+)")

_AFFIRMATIVE_REPLIES = {"oui", "o", "yes", "y"}

# Console dédiée au dialogue de cadrage (phase 1) — appels lents (Opus), sans
# retour visuel entre deux tours autrement (voir docs/roadmap.md, remontée
# utilisateur 2026-07-15) : chaque tour est visuellement délimité par
# _TURN_SEPARATOR, la question du PM et la réponse de l'utilisateur affichées
# dans des couleurs distinctes pour ne pas se perdre en scrollant en arrière.
_cadrage_console = Console()
_TURN_SEPARATOR = "-" * 60


def _specs_dir(config: StudioConfig) -> str:
    return config.get("structure", {}).get("specs_dir", "specs/")


async def _read_optional(path: Path) -> str:
    """Lit un fichier, retourne une chaîne vide s'il n'existe pas (contexte optionnel)."""
    try:
        return await read_card(path)
    except FileNotFoundError:
        return ""


def _will_be_created_by_earlier_agent(
    relative_path: str, agent: str, agent_sequence: list, agent_card_metadata: dict
) -> bool:
    """
    True si `relative_path` figure dans files_to_create/files_to_modify d'un
    agent précédant `agent` dans agent_sequence — auquel cas le fichier
    n'existe pas encore au moment où le PM écrit les fiches (phase 3), mais
    existera bien quand `agent` s'exécutera réellement (phase 4/6, après
    l'agent producteur). Distingue une dépendance légitime entre agents de la
    même séquence d'une référence à un fichier qui n'existera jamais (voir
    docs/roadmap.md, 2026-07-15).
    """
    agent_index = agent_sequence.index(agent)
    for earlier_agent in agent_sequence[:agent_index]:
        earlier_metadata = agent_card_metadata.get(earlier_agent, {})
        if relative_path in earlier_metadata.get("files_to_create", []):
            return True
        if relative_path in earlier_metadata.get("files_to_modify", []):
            return True
    return False


def _extract_feature_name(card_root_content: str) -> str:
    match = _FEATURE_NAME_PATTERN.search(card_root_content)
    if not match:
        raise RuntimeError(
            "card-root.md sans champ **Nom de la feature** (voir "
            "templates/card-root.md.template) — impossible de créer la branche du run"
        )
    return match.group(1).strip()


def _render_imported_card_root(
    run_id: str, project_name: str, feature_name: str, objective_raw: str,
) -> str:
    """
    Synthétise un card-root.md minimal pour le raccourci "import de brief
    existant" (voir _run_brief_import) — pas écrit par le LLM, templating
    Python déterministe (même mécanisme que cli.py::_write_project_config),
    garantissant le champ **Nom de la feature** requis par
    _extract_feature_name (contrat lu ensuite par _run_fiches).
    """
    content = _CARD_ROOT_IMPORT_TEMPLATE_PATH.read_text(encoding="utf-8")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        content.replace("{{RUN_ID}}", run_id)
        .replace("{{DATE}}", today)
        .replace("{{PROJECT_NAME}}", project_name)
        .replace("{{FEATURE_NAME}}", feature_name)
        .replace("{{OBJECTIVE_RAW}}", objective_raw or "(import de brief existant)")
    )


async def _run_validation_dialogue(
    config: StudioConfig,
    tracer: AgentTracer,
    system_prompt: str,
    transcript_seed: list[str],
    draft_label: str,
    confirm_prompt: str,
    model_key: str,
) -> tuple[str, int, int, int, int]:
    """
    Boucle de dialogue QUESTION:/FICHE_VALIDEE: partagée entre _run_cadrage
    (phase 1, depuis un objectif brut) et _run_brief_import (raccourci
    import de brief existant, depuis state.imported_brief_content) — voir
    prompts/pm.md, section Format de sortie. Synchrone (terminal
    input()/print()) : pas d'aller-retour via le mécanisme de checkpoint
    LangGraph/resume, l'utilisateur est déjà présent au terminal à chaque
    tour, donc le "checkpoint" de cette phase est le dialogue lui-même.

    Args:
        transcript_seed: message(s) initial(aux) du transcript (objectif
            brut pour _run_cadrage, document importé + contexte projet pour
            _run_brief_import).
        draft_label: libellé affiché au-dessus du brouillon proposé par le
            PM (ex. "PM (proposition de fiche racine)").
        confirm_prompt: texte exact de la confirmation terminale (ex.
            "\nValider cette fiche racine ? [oui/non] : ").
        model_key: clé dans config.models (les deux appelants utilisent
            "pm_opus" aujourd'hui).

    Returns:
        (draft validé, tokens_prompt_total, tokens_completion_total,
        duration_total_ms, claude_code_calls) — à l'appelant d'écrire les
        fichiers, committer, construire l'AgentResult et avancer la phase.
    """
    transcript = list(transcript_seed)
    claude_code_config = config.get("claude_code", {})
    tokens_prompt_total = 0
    tokens_completion_total = 0
    duration_total_ms = 0
    claude_code_calls = 0

    while True:
        claude_code_calls += 1
        result = await run_claude_code(
            prompt=f"{system_prompt}\n\n---\n\n" + "\n\n".join(transcript),
            model=config.models[model_key],
            cwd=config.repo_path,
            timeout_seconds=claude_code_config.get("timeout_seconds", 300),
            output_format=claude_code_config.get("output_format", "json"),
            tracer=tracer,
        )
        usage = result.get("usage", {})
        tokens_prompt_total += usage.get("input_tokens", 0)
        tokens_completion_total += usage.get("output_tokens", 0)
        duration_total_ms += result.get("duration_ms", 0)
        content = result["content"]

        fiche_match = _FICHE_VALIDEE_PATTERN.search(content)
        if fiche_match:
            draft = fiche_match.group(1).strip()
            _cadrage_console.print(_TURN_SEPARATOR, style="dim")
            _cadrage_console.print(f"[bold cyan]{draft_label}[/bold cyan] :")
            _cadrage_console.print(draft)
            confirmation = input(confirm_prompt).strip().lower()
            _cadrage_console.print(f"[green]Vous :[/green] {confirmation or '(aucune précision)'}")
            if confirmation in _AFFIRMATIVE_REPLIES:
                return (
                    draft, tokens_prompt_total, tokens_completion_total,
                    duration_total_ms, claude_code_calls,
                )

            transcript.append(f"{draft_label} :\n{draft}")
            transcript.append(
                f"Utilisateur : pas encore validé — {confirmation or '(aucune précision)'}"
            )
            continue

        question_match = _QUESTION_PATTERN.search(content)
        question = question_match.group(1).strip() if question_match else content.strip()
        _cadrage_console.print(_TURN_SEPARATOR, style="dim")
        _cadrage_console.print(f"[bold cyan]PM :[/bold cyan] {question}")
        reply = input("> ").strip()
        _cadrage_console.print(f"[green]Vous :[/green] {reply}")
        transcript.append(f"PM : {question}")
        transcript.append(f"Utilisateur : {reply}")


async def _run_cadrage(state: StudioState, config: StudioConfig) -> dict:
    """
    Dialogue de cadrage jusqu'à validation explicite de la fiche racine par
    l'utilisateur — voir prompts/pm.md, section Format de sortie (phase 1),
    et _run_validation_dialogue pour la mécanique du dialogue elle-même.
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    transcript_seed = [f"Objectif initial de l'utilisateur : {state.objective_raw}"]

    tracer = RunTracer.for_run(config, state.run_id).for_agent("pm", state.current_phase)
    tracer.emit("node_enter")

    draft, tokens_prompt_total, tokens_completion_total, duration_total_ms, claude_code_calls = (
        await _run_validation_dialogue(
            config, tracer, system_prompt, transcript_seed,
            draft_label="PM (proposition de fiche racine)",
            confirm_prompt="\nValider cette fiche racine ? [oui/non] : ",
            model_key="pm_opus",
        )
    )

    card_root_relative = str(Path(_specs_dir(config)) / state.run_id / "card-root.md")
    await write_card(config.repo_path / card_root_relative, draft, tracer=tracer)

    agent_result = AgentResult(
        agent="pm",
        phase=state.current_phase,
        status="success",
        output_files=[card_root_relative],
        tokens_prompt=tokens_prompt_total,
        tokens_completion=tokens_completion_total,
        duration_ms=duration_total_ms,
    )
    await record_agent_result(
        config, state, agent_result, model=config.models["pm_opus"],
        claude_code_calls=claude_code_calls,
    )
    tracer.emit("node_exit", status="success", output_files=[card_root_relative])
    return {
        "agent_results": state.agent_results + [agent_result],
        "card_root_path": card_root_relative,
        "current_phase": Phase.AUDIT_AMONT,
        "total_tokens_opus": state.total_tokens_opus + tokens_prompt_total + tokens_completion_total,
    }


async def _run_brief_import(state: StudioState, config: StudioConfig) -> dict:
    """
    Raccourci "import de brief existant" (voir prompts/pm.md, section Import
    de brief existant) : le PM révise directement state.imported_brief_content
    au lieu de dialoguer à partir de state.objective_raw (phase 1), ET saute
    l'audit amont Architecte (phase 2) — décision actée explicitement par
    l'utilisateur (voir docs/roadmap.md). Le brouillon validé devient
    architect-brief.md tel quel ; un card-root.md minimal est synthétisé en
    Python (voir _render_imported_card_root), pas par le LLM.
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    reference_files = config.get("reference_files", {})
    project_map_content = await _read_optional(
        config.repo_path / reference_files.get("project_map", "specs/project-map.md")
    )
    architect_map_content = await _read_optional(
        config.repo_path / reference_files.get("architect_map", "specs/architect-map.md")
    )
    transcript_seed = [
        f"Document importé par l'utilisateur :\n\n{state.imported_brief_content}",
        f"## project-map.md\n\n{project_map_content or '(absent — premier run du projet)'}\n\n"
        f"## architect-map.md\n\n{architect_map_content or '(absent — premier run du projet)'}",
    ]

    tracer = RunTracer.for_run(config, state.run_id).for_agent("pm", state.current_phase)
    tracer.emit("node_enter")

    draft, tokens_prompt_total, tokens_completion_total, duration_total_ms, claude_code_calls = (
        await _run_validation_dialogue(
            config, tracer, system_prompt, transcript_seed,
            draft_label="PM (revue du brief importé)",
            confirm_prompt="\nValider ce brief Architecte ? [oui/non] : ",
            model_key="pm_opus",
        )
    )

    feature_name = _extract_feature_name(draft)

    brief_relative = str(Path(_specs_dir(config)) / state.run_id / "architect-brief.md")
    await write_card(config.repo_path / brief_relative, draft, tracer=tracer)
    await commit_as_agent(
        repo_path=config.repo_path,
        agent="pm",
        message="docs: architect brief (import)",
        files=[brief_relative],
        tracer=tracer,
    )

    card_root_relative = str(Path(_specs_dir(config)) / state.run_id / "card-root.md")
    card_root_content = _render_imported_card_root(
        run_id=state.run_id,
        project_name=state.project_name,
        feature_name=feature_name,
        objective_raw=state.objective_raw,
    )
    await write_card(config.repo_path / card_root_relative, card_root_content, tracer=tracer)

    agent_result = AgentResult(
        agent="pm",
        phase=state.current_phase,
        status="success",
        output_files=[card_root_relative, brief_relative],
        tokens_prompt=tokens_prompt_total,
        tokens_completion=tokens_completion_total,
        duration_ms=duration_total_ms,
    )
    await record_agent_result(
        config, state, agent_result, model=config.models["pm_opus"],
        claude_code_calls=claude_code_calls,
    )
    tracer.emit("node_exit", status="success", output_files=[card_root_relative, brief_relative])

    return {
        "agent_results": state.agent_results + [agent_result],
        "card_root_path": card_root_relative,
        "architect_brief_path": brief_relative,
        "current_phase": Phase.FICHES,
        "agent_cards": {},
        "total_tokens_opus": state.total_tokens_opus + tokens_prompt_total + tokens_completion_total,
    }


async def _create_branch_and_advance(
    state: StudioState, config: StudioConfig, feature_name: str, agent_cards: dict,
    tracer: Optional[AgentTracer] = None,
) -> dict:
    """Crée la branche du run et commit les fiches (fin de phase 3, voir ADR 0007)."""
    branch_name = await create_run_branch(
        config.repo_path, feature_name,
        base_branch=config.get("git", {}).get("base_branch", "develop"),
    )
    commit_files = sorted(agent_cards.values()) + [state.card_root_path]
    await commit_as_agent(
        repo_path=config.repo_path,
        agent="pm",
        message=f"docs: fiches dependantes - {feature_name}",
        files=commit_files,
        tracer=tracer,
    )
    return {
        "branch_name": branch_name,
        "current_phase": Phase.STUBS,
        "current_agent_index": 0,
    }


async def _run_fiches(state: StudioState, config: StudioConfig) -> dict:
    """
    Phase 3 en deux passes, pour respecter l'ordre documenté (docs/workflow.md :
    « à la validation de cette phase, la branche du run est créée ») :

    1. Première invocation (state.agent_cards vide) : génère et écrit les
       fiches dépendantes. Si should_checkpoint(state) : s'arrête là
       (WAITING_HUMAN), sans créer la branche.
    2. Reprise (state.agent_cards déjà rempli, via `devaimazing resume`) :
       les fiches existent déjà sur disque, ce node se contente de créer
       la branche et de commiter.

    Si la réponse du PM ne respecte pas le contrat de sortie (aucun bloc de
    fichier reconnu, fiche manquante pour un agent, fiche sans section
    Feedback, ou existing_files_to_read référençant un chemin qui n'existe ni
    sur disque ni dans les files_to_create/files_to_modify d'un agent
    antérieur de la séquence) : dégradation gracieuse plutôt qu'échec net
    (alignée sur le traitement déjà appliqué à Back/Front/Test, voir
    nodes/backend.py) — state.status=RunStatus.WAITING_HUMAN, aucune fiche
    écrite, aucune progression d'état, pour qu'une reprise
    (`devaimazing resume`) retente l'appel depuis le début. Bornée par
    agents.max_iterations (comme les autres agents producteurs) : au-delà,
    RunStatus.FAILED sans nouvel appel LLM.

    Un chemin de existing_files_to_read référençant un fichier que
    créera/modifiera un agent antérieur dans agent_sequence (ex. back-tu lit
    backend/main.py, produit par back qui le précède) est valide même s'il
    n'existe pas encore sur disque au moment de l'écriture des fiches — voir
    _will_be_created_by_earlier_agent et docs/roadmap.md, 2026-07-15.

    Génération en deux appels Claude Code CLI séparés (voir docs/roadmap.md,
    2026-07-15) : un appel contraint par schéma JSON demandant à la fois du
    JSON et du texte libre en parallèle s'est révélé peu fiable en pratique
    (le modèle ne produit que le JSON, jamais le texte) — remplacé par un
    appel « métadonnées » (schéma seul) suivi d'un appel « fiches » (prose
    seule, recevant les métadonnées du premier appel en entrée pour rester
    cohérent). Les deux appels sont refaits intégralement à chaque tentative
    (y compris en cas de reprise après un feedback_sent) — pas de mémorisation
    intermédiaire du premier appel, décision actée pour garder l'état simple.
    """
    tracer = RunTracer.for_run(config, state.run_id).for_agent("pm", state.current_phase)
    tracer.emit("node_enter", card=state.card_root_path)

    card_root_content = await read_card(config.repo_path / state.card_root_path, tracer=tracer)
    feature_name = _extract_feature_name(card_root_content)

    if state.agent_cards:
        updates = await _create_branch_and_advance(
            state, config, feature_name, state.agent_cards, tracer=tracer
        )
        tracer.emit("node_exit", status="success", branch_name=updates.get("branch_name"))
        return updates

    if max_iterations_exceeded(state, config, "pm"):
        max_iterations = config.get("agents", {}).get("max_iterations", 3)
        tracer.emit("node_exit", status="failed", reason="max_iterations_exceeded")
        return {
            "status": RunStatus.FAILED,
            "requires_manual_intervention": True,
            "intervention_reason": (
                f"Agent 'pm' a atteint la limite de {max_iterations} itérations "
                f"en phase {state.current_phase.name} sans succès."
            ),
            "failed_agents": (
                state.failed_agents if "pm" in state.failed_agents
                else state.failed_agents + ["pm"]
            ),
        }

    architect_brief_content = await read_card(
        config.repo_path / state.architect_brief_path, tracer=tracer
    )

    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    claude_code_config = config.get("claude_code", {})

    # Étape 1 — métadonnées structurées (JSON schema uniquement, aucun texte libre).
    metadata_user_prompt = (
        "## Phase 3 - Fiches dépendantes - Étape 1 : métadonnées structurées\n\n"
        f"## card-root.md\n\n{card_root_content}\n\n"
        f"## architect-brief.md\n\n{architect_brief_content}\n\n"
        f"Run ID : {state.run_id}.\n\n"
        "Pour cette réponse, produis UNIQUEMENT le JSON structuré (sequence + cards) "
        "conforme au schéma — aucun bloc <<<DEVAIMAZING_FILE>>>, aucun texte libre. Le "
        "contenu des fiches sera demandé dans un appel séparé (étape 2)."
    )
    metadata_result = await run_claude_code(
        prompt=f"{system_prompt}\n\n---\n\n{metadata_user_prompt}",
        model=config.models["pm_sonnet"],
        cwd=config.repo_path,
        timeout_seconds=claude_code_config.get("timeout_seconds", 300),
        output_format=claude_code_config.get("output_format", "json"),
        response_schema=PM_FICHES_SCHEMA,
        tracer=tracer,
    )

    try:
        agent_sequence, agent_card_metadata = parse_pm_structured_output(
            metadata_result.get("structured_output")
        )
    except ValueError as exc:
        raise RuntimeError(f"Réponse du PM (phase 3, étape métadonnées) invalide : {exc}") from exc

    iteration = agent_iteration_count(state, "pm") + 1

    # Étape 2 — contenu des fiches en prose, cohérent avec les métadonnées de l'étape 1.
    fiches_user_prompt = (
        "## Phase 3 - Fiches dépendantes - Étape 2 : contenu des fiches\n\n"
        f"## card-root.md\n\n{card_root_content}\n\n"
        f"## architect-brief.md\n\n{architect_brief_content}\n\n"
        "## Séquence et métadonnées déjà déterminées (à respecter strictement)\n\n"
        f"{json.dumps(metadata_result.get('structured_output'), ensure_ascii=False, indent=2)}\n\n"
        f"Run ID : {state.run_id}.\n\n"
        "Pour cette réponse, produis UNIQUEMENT les blocs <<<DEVAIMAZING_FILE>>> des "
        "fiches, un par agent de la séquence ci-dessus — aucun JSON n'est demandé ici."
    )
    result = await run_claude_code(
        prompt=f"{system_prompt}\n\n---\n\n{fiches_user_prompt}",
        model=config.models["pm_sonnet"],
        cwd=config.repo_path,
        timeout_seconds=claude_code_config.get("timeout_seconds", 300),
        output_format=claude_code_config.get("output_format", "json"),
        tracer=tracer,
    )
    content = result["content"]

    combined_tokens_prompt = (
        metadata_result.get("usage", {}).get("input_tokens", 0)
        + result.get("usage", {}).get("input_tokens", 0)
    )
    combined_tokens_completion = (
        metadata_result.get("usage", {}).get("output_tokens", 0)
        + result.get("usage", {}).get("output_tokens", 0)
    )
    combined_duration_ms = metadata_result.get("duration_ms", 0) + result.get("duration_ms", 0)

    try:
        files = parse_agent_file_blocks(content, tracer=tracer)
        agent_cards = {}
        for agent in agent_sequence:
            expected_path = str(Path(_specs_dir(config)) / state.run_id / f"{agent}.md")
            if expected_path not in files:
                raise RuntimeError(
                    f"Fiche manquante pour l'agent {agent!r} : bloc attendu à {expected_path!r} "
                    "absent de la réponse du PM (voir prompts/pm.md, section Format de sortie — phase 3)"
                )
            agent_cards[agent] = expected_path

        for agent, expected_path in agent_cards.items():
            if FEEDBACK_HEADING not in files[expected_path]:
                raise RuntimeError(
                    f"Fiche produite pour l'agent {agent!r} ({expected_path!r}) sans section "
                    f"'{FEEDBACK_HEADING}' — contrat requis par templates/card-agent.md.template "
                    "(l'Architecte et Sécu y annotent leurs écarts via append_feedback, voir "
                    "prompts/pm.md, section Format de sortie — phase 3)"
                )

        for agent, metadata in agent_card_metadata.items():
            for relative_path in metadata["existing_files_to_read"]:
                if (config.repo_path / relative_path).is_file():
                    continue
                if _will_be_created_by_earlier_agent(
                    relative_path, agent, agent_sequence, agent_card_metadata
                ):
                    continue
                raise RuntimeError(
                    f"Fiche produite pour l'agent {agent!r} référence, dans "
                    f"existing_files_to_read, {relative_path!r} qui n'existe pas dans le repo "
                    f"cible ({config.repo_path}) et n'est produit par aucun agent antérieur de "
                    "la séquence — corriger la fiche (fichier inexistant à retirer, ou à "
                    "déplacer vers files_to_create s'il doit être créé) avant toute écriture "
                    "(voir prompts/pm.md, section Format de sortie — phase 3)"
                )
    except (ValueError, RuntimeError) as exc:
        agent_result = AgentResult(
            agent="pm",
            phase=state.current_phase,
            status="feedback_sent",
            feedback=(
                f"{exc}\n\n--- Contenu brut produit par l'agent (étape fiches) ---\n{content}"
            ),
            iteration=iteration,
            tokens_prompt=combined_tokens_prompt,
            tokens_completion=combined_tokens_completion,
            duration_ms=combined_duration_ms,
        )
        await record_agent_result(
            config, state, agent_result, model=config.models["pm_sonnet"], claude_code_calls=2
        )
        tracer.emit("node_exit", status="feedback_sent")
        return {
            "agent_results": state.agent_results + [agent_result],
            "status": RunStatus.WAITING_HUMAN,
            "awaiting_human_validation": True,
            "total_tokens_sonnet": (
                state.total_tokens_sonnet + combined_tokens_prompt + combined_tokens_completion
            ),
        }

    for relative_path, file_content in files.items():
        await write_card(config.repo_path / relative_path, file_content, tracer=tracer)

    agent_result = AgentResult(
        agent="pm",
        phase=state.current_phase,
        status="success",
        output_files=sorted(files.keys()),
        iteration=iteration,
        tokens_prompt=combined_tokens_prompt,
        tokens_completion=combined_tokens_completion,
        duration_ms=combined_duration_ms,
    )
    await record_agent_result(config, state, agent_result, model=config.models["pm_sonnet"], claude_code_calls=2)

    updates: dict = {
        "agent_results": state.agent_results + [agent_result],
        "agent_cards": agent_cards,
        "agent_sequence": agent_sequence,
        "agent_card_metadata": agent_card_metadata,
        "total_tokens_sonnet": (
            state.total_tokens_sonnet + combined_tokens_prompt + combined_tokens_completion
        ),
    }

    if should_checkpoint(state):
        updates["status"] = RunStatus.WAITING_HUMAN
        updates["awaiting_human_validation"] = True
        tracer.emit("node_exit", status="waiting_human", output_files=sorted(files.keys()))
        return updates

    # Pas de checkpoint configuré pour cette phase : la branche est créée
    # immédiatement, sans attendre une reprise.
    branch_updates = await _create_branch_and_advance(
        state, config, feature_name, agent_cards, tracer=tracer
    )
    tracer.emit("node_exit", status="success", output_files=sorted(files.keys()))
    return {**updates, **branch_updates}


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node PM.

    Args:
        state: État courant du run. state.current_phase détermine le
            comportement (voir description du module). state.objective_raw
            doit être renseigné dès Phase.RECEPTION.

    Returns:
        État mis à jour :
        - En Phase.RECEPTION/Phase.CADRAGE : le dialogue tourne
          entièrement dans cet appel (terminal synchrone) jusqu'à
          validation explicite ; state.card_root_path renseigné,
          state.current_phase=Phase.AUDIT_AMONT en retour. Si
          state.imported_brief_content est renseigné : state.card_root_path
          (synthétisé) ET state.architect_brief_path (le document importé,
          validé) sont renseignés, state.agent_cards remis à {}, et
          state.current_phase=Phase.FICHES directement (phases 1 et 2
          sautées).
        - En Phase.FICHES, première invocation (state.agent_cards vide) :
          state.agent_cards et state.agent_sequence renseignés. Si
          should_checkpoint(state) : state.status=RunStatus.WAITING_HUMAN,
          state.current_phase reste Phase.FICHES (branche pas encore
          créée). Sinon (ou à la reprise, state.agent_cards déjà rempli) :
          state.branch_name renseigné, state.current_phase=Phase.STUBS.
        Dans tous les cas menant à une écriture de fiche, un AgentResult
        est ajouté à state.agent_results.

    Raises:
        RuntimeError: Si l'appel à Claude Code CLI échoue (voir
            tools/claude_code.py::run_claude_code), si le structured_output de
            phase 3 est absent/invalide (voir
            tools/filesystem.py::parse_pm_structured_output), si un chemin de
            existing_files_to_read référencé n'existe pas dans le repo cible,
            si une fiche est manquante ou sans section Feedback pour un agent
            de la séquence, ou si card-root.md n'a pas de champ **Nom de la
            feature**.
        TimeoutError: Si l'appel dépasse claude_code.timeout_seconds
            (config/studio.yml).
        FileNotFoundError: Si card-root.md ou architect-brief.md est
            introuvable en Phase.FICHES.
        KeyError: Si state.current_phase n'est ni RECEPTION, ni CADRAGE, ni
            FICHES.

    Side effects:
        - Appelle tools.claude_code.run_claude_code (modèle models.pm_opus
          en Phase.CADRAGE, models.pm_sonnet en Phase.FICHES) — plusieurs
          fois en Phase.CADRAGE (une fois par tour de dialogue), deux fois
          en Phase.FICHES (métadonnées structurées puis contenu des fiches,
          voir docs/roadmap.md 2026-07-15).
        - Lit et écrit au terminal (input()/print()) pendant le dialogue de
          cadrage — seul node de tout le studio à le faire.
        - Écrit specs/<specs_dir>/run-<run_id>/card-root.md (phase 1) et
          une fiche par agent (phase 3) via tools.filesystem.write_card.
        - Crée la branche du run via tools.git.create_run_branch,
          uniquement à la validation effective de la Phase.FICHES (jamais
          pendant le dialogue de cadrage — voir docs/workflow.md phase 1).
        - Incrémente state.total_tokens_opus (Phase.CADRAGE, cumulé sur
          tous les tours) ou state.total_tokens_sonnet (Phase.FICHES).

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     project_name="webaimazing-v2",
        ...     objective_raw="ajouter un endpoint de login",
        ...     current_phase=Phase.CADRAGE,
        ... )
        >>> state = await run(state)  # doctest: +SKIP
        >>> state.card_root_path
        'specs/run-042/card-root.md'

    Notes:
        Un trou d'intention détecté par la checklist (docs/workflow.md
        phase 1) ne doit jamais être comblé par une valeur par défaut
        « raisonnable » — il est noté dans la section "Questions en
        suspens" de la fiche par le PM lui-même (prompts/pm.md), pas
        détecté par du code Python ici (ADR 0008, garde-fou non
        négociable).

        La config du run (repo_path, modèles, chemins) est chargée via
        StudioConfig.from_env(), pas transmise dans StudioState.

        Chaque activation menant à une écriture de fiche est enregistrée
        via studio.metrics.record_agent_result (claude_code_calls compte
        tous les tours du dialogue en Phase.CADRAGE, pas seulement le
        dernier).
    """
    config = StudioConfig.from_env()

    if state.current_phase in (Phase.RECEPTION, Phase.CADRAGE):
        if state.imported_brief_content:
            return await _run_brief_import(state, config)
        return await _run_cadrage(state, config)
    if state.current_phase == Phase.FICHES:
        return await _run_fiches(state, config)

    raise KeyError(
        f"Phase non gérée par le node PM : {state.current_phase!r} "
        f"(attendu RECEPTION, CADRAGE ou FICHES)"
    )
