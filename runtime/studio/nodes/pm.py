"""
Node LangGraph - Agent PM.

Seul agent stateful du studio (mémoire portée par le checkpointer SQLite
LangGraph, voir ADR 0001 et ADR 0003). Ce node couvre deux activations
distinctes du même agent, distinguées par state.current_phase :

- Phase.RECEPTION / Phase.CADRAGE (phases 0-1, modèle models.pm_opus) :
  dialogue de raffinement itératif avec l'utilisateur jusqu'à validation
  de la fiche racine (voir docs/workflow.md phase 1, checklist d'intention,
  ADR 0008).
- Phase.FICHES (phase 3, modèle models.pm_sonnet) : définition de la
  séquence d'agents et écriture d'une fiche par agent, puis création de
  la branche du run (premier commit-point, voir ADR 0007).

Le node ne couvre pas la phase 10 (clôture) : celle-ci est gérée par
studio.nodes.closer, Python pur sans appel LLM.
"""

import re
from pathlib import Path

from studio.config import StudioConfig
from studio.metrics import record_agent_result
from studio.routing import should_checkpoint
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

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _DEVAIMAZING_ROOT / "prompts" / "pm.md"

_QUESTION_PATTERN = re.compile(r"QUESTION:\s*(.+)", re.DOTALL)
_FICHE_VALIDEE_PATTERN = re.compile(r"FICHE_VALIDEE:\s*\n(.*)", re.DOTALL)
_FEATURE_NAME_PATTERN = re.compile(r"\*\*Nom de la feature\*\*\s*:\s*(.+)")

_AFFIRMATIVE_REPLIES = {"oui", "o", "yes", "y"}


def _specs_dir(config: StudioConfig) -> str:
    return config.get("structure", {}).get("specs_dir", "specs/")


def _extract_feature_name(card_root_content: str) -> str:
    match = _FEATURE_NAME_PATTERN.search(card_root_content)
    if not match:
        raise RuntimeError(
            "card-root.md sans champ **Nom de la feature** (voir "
            "templates/card-root.md.template) — impossible de créer la branche du run"
        )
    return match.group(1).strip()


async def _run_cadrage(state: StudioState, config: StudioConfig) -> dict:
    """
    Dialogue de cadrage synchrone (terminal input()/print()) jusqu'à
    validation explicite de la fiche racine par l'utilisateur — voir
    prompts/pm.md, section Format de sortie (phase 1).

    Toute la boucle de raffinement se déroule dans cet unique appel de
    node (pas d'aller-retour via le mécanisme de checkpoint LangGraph/
    resume : l'utilisateur est déjà présent au terminal à chaque tour,
    donc le "checkpoint" de cette phase est le dialogue lui-même).
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    transcript = [f"Objectif initial de l'utilisateur : {state.objective_raw}"]
    claude_code_config = config.get("claude_code", {})
    tokens_prompt_total = 0
    tokens_completion_total = 0
    duration_total_ms = 0
    claude_code_calls = 0

    while True:
        claude_code_calls += 1
        result = await run_claude_code(
            prompt=f"{system_prompt}\n\n---\n\n" + "\n\n".join(transcript),
            model=config.models["pm_opus"],
            cwd=config.repo_path,
            timeout_seconds=claude_code_config.get("timeout_seconds", 300),
            output_format=claude_code_config.get("output_format", "json"),
        )
        usage = result.get("usage", {})
        tokens_prompt_total += usage.get("input_tokens", 0)
        tokens_completion_total += usage.get("output_tokens", 0)
        duration_total_ms += result.get("duration_ms", 0)
        content = result["content"]

        fiche_match = _FICHE_VALIDEE_PATTERN.search(content)
        if fiche_match:
            draft = fiche_match.group(1).strip()
            print(f"\n{draft}\n")
            confirmation = input("Valider cette fiche racine ? [oui/non] : ").strip().lower()
            if confirmation in _AFFIRMATIVE_REPLIES:
                card_root_relative = str(Path(_specs_dir(config)) / state.run_id / "card-root.md")
                await write_card(config.repo_path / card_root_relative, draft)

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
                return {
                    "agent_results": state.agent_results + [agent_result],
                    "card_root_path": card_root_relative,
                    "current_phase": Phase.AUDIT_AMONT,
                    "total_tokens_opus": state.total_tokens_opus + tokens_prompt_total + tokens_completion_total,
                }

            transcript.append(f"PM (proposition de fiche racine) :\n{draft}")
            transcript.append(
                f"Utilisateur : pas encore validé — {confirmation or '(aucune précision)'}"
            )
            continue

        question_match = _QUESTION_PATTERN.search(content)
        question = question_match.group(1).strip() if question_match else content.strip()
        print(f"\nPM : {question}")
        reply = input("> ").strip()
        transcript.append(f"PM : {question}")
        transcript.append(f"Utilisateur : {reply}")


async def _create_branch_and_advance(
    state: StudioState, config: StudioConfig, feature_name: str, agent_cards: dict
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
    """
    card_root_content = await read_card(config.repo_path / state.card_root_path)
    feature_name = _extract_feature_name(card_root_content)

    if state.agent_cards:
        return await _create_branch_and_advance(state, config, feature_name, state.agent_cards)

    architect_brief_content = await read_card(config.repo_path / state.architect_brief_path)

    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = (
        "## Phase 3 - Fiches dépendantes\n\n"
        f"## card-root.md\n\n{card_root_content}\n\n"
        f"## architect-brief.md\n\n{architect_brief_content}\n\n"
        f"Run ID : {state.run_id}."
    )
    claude_code_config = config.get("claude_code", {})
    result = await run_claude_code(
        prompt=f"{system_prompt}\n\n---\n\n{user_prompt}",
        model=config.models["pm_sonnet"],
        cwd=config.repo_path,
        timeout_seconds=claude_code_config.get("timeout_seconds", 300),
        output_format=claude_code_config.get("output_format", "json"),
        response_schema=PM_FICHES_SCHEMA,
    )
    content = result["content"]

    try:
        agent_sequence, agent_card_metadata = parse_pm_structured_output(
            result.get("structured_output")
        )
    except ValueError as exc:
        raise RuntimeError(f"Réponse du PM (phase 3) invalide : {exc}") from exc

    files = parse_agent_file_blocks(content)
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
            if not (config.repo_path / relative_path).is_file():
                raise RuntimeError(
                    f"Fiche produite pour l'agent {agent!r} référence, dans "
                    f"existing_files_to_read, {relative_path!r} qui n'existe pas dans le repo "
                    f"cible ({config.repo_path}) — corriger la fiche (fichier inexistant à "
                    "retirer, ou à déplacer vers files_to_create s'il doit être créé) avant "
                    "toute écriture (voir prompts/pm.md, section Format de sortie — phase 3)"
                )

    for relative_path, file_content in files.items():
        await write_card(config.repo_path / relative_path, file_content)

    usage = result.get("usage", {})
    agent_result = AgentResult(
        agent="pm",
        phase=state.current_phase,
        status="success",
        output_files=sorted(files.keys()),
        tokens_prompt=usage.get("input_tokens", 0),
        tokens_completion=usage.get("output_tokens", 0),
        duration_ms=result.get("duration_ms", 0),
    )
    await record_agent_result(config, state, agent_result, model=config.models["pm_sonnet"], claude_code_calls=1)

    updates: dict = {
        "agent_results": state.agent_results + [agent_result],
        "agent_cards": agent_cards,
        "agent_sequence": agent_sequence,
        "agent_card_metadata": agent_card_metadata,
        "total_tokens_sonnet": (
            state.total_tokens_sonnet + usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        ),
    }

    if should_checkpoint(state):
        updates["status"] = RunStatus.WAITING_HUMAN
        updates["awaiting_human_validation"] = True
        return updates

    # Pas de checkpoint configuré pour cette phase : la branche est créée
    # immédiatement, sans attendre une reprise.
    branch_updates = await _create_branch_and_advance(state, config, feature_name, agent_cards)
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
          state.current_phase=Phase.AUDIT_AMONT en retour.
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
          fois en Phase.CADRAGE (une fois par tour de dialogue).
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
        return await _run_cadrage(state, config)
    if state.current_phase == Phase.FICHES:
        return await _run_fiches(state, config)

    raise KeyError(
        f"Phase non gérée par le node PM : {state.current_phase!r} "
        f"(attendu RECEPTION, CADRAGE ou FICHES)"
    )
