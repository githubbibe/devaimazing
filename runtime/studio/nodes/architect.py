"""
Node LangGraph - Agent Architecte.

Agent stateless (ADR 0001) tournant sur Sonnet (models.agent_auditor, voir
docs/llm-strategy.md — principe auditeur/producteur, ARCHITECTURE.md
principe 4). Ce node couvre trois activations distinctes selon
state.current_phase :

- Phase.AUDIT_AMONT (phase 2) : produit le brief architectural
  (specs/run-NNN/architect-brief.md) à partir de card-root.md et des
  cartes projet (project-map.md, architect-map.md).
- Phase.AUDIT_STUBS (phase 5) : audite les stubs produits par Back/Front
  (phase 4), annote la fiche de l'agent fautif si écart détecté.
- Phase.AUDIT_AVAL (phase 9) : audite la conformité finale, produit la
  documentation complète (ADR, OpenAPI, README, CHANGELOG, runbooks) et
  détecte la factorisation à planifier (sans la faire).
"""

import re
from pathlib import Path
from typing import Optional

from studio.config import StudioConfig
from studio.metrics import record_agent_result
from studio.routing import PHASE_AGENT_ROLES, agent_iteration_count, should_checkpoint
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.claude_code import run_claude_code
from studio.tools.filesystem import (
    append_feedback,
    inject_skills,
    parse_agent_file_blocks,
    read_card,
    write_card,
)
from studio.tools.git import commit_as_agent
from studio.tools.tracer import AgentTracer, RunTracer

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _DEVAIMAZING_ROOT / "prompts" / "architect.md"
_SKILLS_DIR = _DEVAIMAZING_ROOT / "skills"
_SKILL_NAMES = ["documentation", "factorization", "retry-patterns", "logging-conventions", "error-handling"]

_STATUT_PATTERN = re.compile(r"STATUT:\s*(CONFORME|ECART)", re.IGNORECASE)
_AGENT_PATTERN = re.compile(r"AGENT:\s*(\S+)")
_FEEDBACK_PATTERN = re.compile(r"FEEDBACK:\s*(.+)", re.DOTALL)

# Chemins de fichiers cités entre backticks dans le feedback de l'Architecte
# (ex. « `backend/schemas.py` — TaskResponse hérite... »), distingués des
# symboles de code (`TaskResponse`, `HTTPException`) par une extension
# reconnue. Voir _extract_feedback_files.
_BACKTICKED_FILE_PATTERN = re.compile(
    r"`([^`]+\.(?:py|md|txt|json|ya?ml|tsx?|jsx?|html|css))`"
)


def _extract_feedback_files(feedback: str, known_files: list[str]) -> list[str]:
    """
    Extrait du texte de feedback de l'Architecte les chemins de fichiers
    qu'il cite entre backticks, résolus contre `known_files` (le périmètre
    déclaré de l'agent fautif — state.agent_card_metadata[faulty_agent],
    files_to_create + files_to_modify) pour permettre une correction
    ciblée (voir StudioState.retry_scope) au lieu d'une régénération
    complète — gap trouvé en run réel le 2026-07-20 sur run-20260714-205712 :
    un redo Architecte faisait toujours régénérer tout le périmètre de
    l'agent fautif, même pour un écart sur un seul fichier (ex.
    `fastapi==0.95.3` réintroduit 3 fois via ce chemin).

    L'Architecte cite parfois un chemin complet (`backend/schemas.py`),
    parfois juste le nom de fichier (`crud.py`) — la résolution contre
    `known_files` (chemin exact, ou match unique par nom de fichier)
    évite de générer un chemin incorrect ; un candidat non résolu de
    façon certaine est ignoré plutôt que deviné.

    Returns:
        Chemins relatifs résolus, dédupliqués, dans l'ordre d'apparition.
        Liste vide si aucun chemin n'est extractible ou résolvable —
        l'appelant doit alors se rabattre sur la régénération complète.
    """
    resolved: list[str] = []
    for candidate in _BACKTICKED_FILE_PATTERN.findall(feedback):
        if candidate in known_files and candidate not in resolved:
            resolved.append(candidate)
            continue
        matches = [f for f in known_files if f == candidate or f.endswith(f"/{candidate}")]
        if len(matches) == 1 and matches[0] not in resolved:
            resolved.append(matches[0])
    return resolved


async def _read_optional(path: Path) -> str:
    """Lit un fichier, retourne une chaîne vide s'il n'existe pas (contexte optionnel)."""
    try:
        return await read_card(path)
    except FileNotFoundError:
        return ""


def _specs_dir(config: StudioConfig) -> str:
    return config.get("structure", {}).get("specs_dir", "specs/")


def _parse_audit_decision(text: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Parse la réponse de l'Architecte en phase 5 (voir prompts/architect.md,
    section Format de sortie — phase 5).

    Returns:
        (conforme, agent_fautif, feedback). agent_fautif et feedback sont
        None si conforme est True.

    Raises:
        RuntimeError: Si la réponse ne contient pas de STATUT reconnu, ou
            signale un écart sans AGENT/FEEDBACK exploitable.
    """
    statut_match = _STATUT_PATTERN.search(text)
    if not statut_match:
        raise RuntimeError(
            "Réponse de l'Architecte (phase 5) sans STATUT reconnu (voir "
            "prompts/architect.md, section Format de sortie — phase 5)"
        )
    if statut_match.group(1).upper() == "CONFORME":
        return True, None, None

    agent_match = _AGENT_PATTERN.search(text)
    feedback_match = _FEEDBACK_PATTERN.search(text)
    if not agent_match or not feedback_match:
        raise RuntimeError(
            "Réponse de l'Architecte (phase 5) signale un écart sans AGENT/FEEDBACK "
            "exploitable (voir prompts/architect.md, section Format de sortie — phase 5)"
        )
    return False, agent_match.group(1), feedback_match.group(1).strip()


async def _build_system_prompt() -> str:
    return await inject_skills(
        base_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        skill_names=_SKILL_NAMES,
        skills_dir=_SKILLS_DIR,
    )


async def _call_architect(
    config: StudioConfig, user_prompt: str, tracer: Optional[AgentTracer] = None
) -> dict:
    system_prompt = await _build_system_prompt()
    claude_code_config = config.get("claude_code", {})
    return await run_claude_code(
        prompt=f"{system_prompt}\n\n---\n\n{user_prompt}",
        model=config.models["agent_auditor"],
        cwd=config.repo_path,
        timeout_seconds=claude_code_config.get("timeout_seconds", 300),
        output_format=claude_code_config.get("output_format", "json"),
        tracer=tracer,
    )


async def _make_agent_result(
    state: StudioState, config: StudioConfig, result: dict, output_files: list[str]
) -> AgentResult:
    usage = result.get("usage", {})
    agent_result = AgentResult(
        agent="architect",
        phase=state.current_phase,
        status="success",
        output_files=output_files,
        iteration=agent_iteration_count(state, "architect") + 1,
        tokens_prompt=usage.get("input_tokens", 0),
        tokens_completion=usage.get("output_tokens", 0),
        duration_ms=result.get("duration_ms", 0),
    )
    await record_agent_result(
        config, state, agent_result, model=config.models["agent_auditor"], claude_code_calls=1
    )
    return agent_result


def _with_checkpoint(state: StudioState, updates: dict) -> dict:
    """Ajoute status/awaiting_human_validation à `updates` si should_checkpoint(state)."""
    if should_checkpoint(state):
        updates["status"] = RunStatus.WAITING_HUMAN
        updates["awaiting_human_validation"] = True
    return updates


async def _run_audit_amont(state: StudioState, config: StudioConfig, tracer: AgentTracer) -> dict:
    reference_files = config.get("reference_files", {})
    card_root_content = await read_card(config.repo_path / state.card_root_path, tracer=tracer)
    project_map_content = await _read_optional(
        config.repo_path / reference_files.get("project_map", "specs/project-map.md")
    )
    architect_map_content = await _read_optional(
        config.repo_path / reference_files.get("architect_map", "specs/architect-map.md")
    )

    user_prompt = (
        "## Phase 2 - Audit amont\n\n"
        f"## card-root.md\n\n{card_root_content}\n\n"
        f"## project-map.md\n\n{project_map_content or '(absent — premier run du projet)'}\n\n"
        f"## architect-map.md\n\n{architect_map_content or '(absent — premier run du projet)'}"
    )
    result = await _call_architect(config, user_prompt, tracer=tracer)

    brief_relative = str(Path(_specs_dir(config)) / state.run_id / "architect-brief.md")
    await write_card(config.repo_path / brief_relative, result["content"], tracer=tracer)
    await commit_as_agent(
        repo_path=config.repo_path,
        agent="architect",
        message="docs: architect brief",
        files=[brief_relative],
        tracer=tracer,
    )

    usage = result.get("usage", {})
    agent_result = await _make_agent_result(state, config, result, [brief_relative])
    updates: dict = {
        "agent_results": state.agent_results + [agent_result],
        "architect_brief_path": brief_relative,
        "current_phase": Phase.FICHES,
        "total_tokens_sonnet": (
            state.total_tokens_sonnet + usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        ),
    }
    tracer.emit("node_exit", status="success", output_files=[brief_relative])
    return _with_checkpoint(state, updates)


async def _run_audit_stubs(state: StudioState, config: StudioConfig, tracer: AgentTracer) -> dict:
    architect_brief_content = await read_card(
        config.repo_path / state.architect_brief_path, tracer=tracer
    )

    stub_agents = [a for a in state.agent_sequence if a in PHASE_AGENT_ROLES[Phase.STUBS]]
    cards_parts = []
    for agent in stub_agents:
        content = await read_card(config.repo_path / state.agent_cards[agent])
        cards_parts.append(f"## Fiche {agent}\n\n{content}")

    user_prompt = (
        "## Phase 5 - Audit des stubs\n\n"
        f"## architect-brief.md\n\n{architect_brief_content}\n\n"
        + "\n\n".join(cards_parts)
        + "\n\nLes stubs eux-mêmes sont dans le repo (accès direct depuis ton cwd)."
    )
    result = await _call_architect(config, user_prompt, tracer=tracer)
    conforme, faulty_agent, feedback = _parse_audit_decision(result["content"])

    usage = result.get("usage", {})
    agent_result = await _make_agent_result(state, config, result, [])
    updates: dict = {
        "agent_results": state.agent_results + [agent_result],
        "total_tokens_sonnet": (
            state.total_tokens_sonnet + usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        ),
    }

    if conforme:
        updates["current_phase"] = Phase.IMPLEMENTATION
        updates["current_agent_index"] = 0
        tracer.emit("node_exit", status="success", conforme=True)
    else:
        faulty_card_path = config.repo_path / state.agent_cards[faulty_agent]
        await append_feedback(faulty_card_path, agent_source="architect", feedback=feedback)
        updates["current_phase"] = Phase.STUBS
        # Index dans state.agent_sequence (la séquence complète) — pas une
        # sous-liste filtrée par phase, voir routing.py::PHASE_AGENT_ROLES
        # pour l'historique du bug de décalage d'indexation que ça causait.
        updates["current_agent_index"] = state.agent_sequence.index(faulty_agent)
        updates["failed_agents"] = state.failed_agents + [faulty_agent]

        metadata = state.agent_card_metadata.get(faulty_agent, {})
        known_files = metadata.get("files_to_create", []) + metadata.get("files_to_modify", [])
        flagged_files = _extract_feedback_files(feedback, known_files)
        if flagged_files:
            # Fichiers identifiés avec une confiance suffisante (résolus
            # contre le périmètre déclaré) : correction ciblée au prochain
            # tour au lieu d'une régénération complète du périmètre entier.
            new_retry_scope = dict(state.retry_scope)
            new_retry_scope[faulty_agent] = {f: feedback for f in flagged_files}
            updates["retry_scope"] = new_retry_scope

        tracer.emit(
            "node_exit",
            status="success",
            conforme=False,
            faulty_agent=faulty_agent,
            flagged_files=flagged_files,
        )

    return _with_checkpoint(state, updates)


async def _run_audit_aval(state: StudioState, config: StudioConfig, tracer: AgentTracer) -> dict:
    specs_dir = _specs_dir(config)
    security_report_content = await _read_optional(
        config.repo_path / specs_dir / state.run_id / "security-report.md"
    )

    cards_parts = []
    for agent, card_relative in state.agent_cards.items():
        content = await _read_optional(config.repo_path / card_relative)
        cards_parts.append(f"## Fiche {agent}\n\n{content}")

    user_prompt = (
        "## Phase 9 - Audit aval\n\n"
        f"## security-report.md\n\n{security_report_content or '(absent)'}\n\n"
        + "\n\n".join(cards_parts)
        + "\n\nLe code complet est dans le repo (accès direct depuis ton cwd)."
    )
    result = await _call_architect(config, user_prompt, tracer=tracer)
    files = parse_agent_file_blocks(result["content"], tracer=tracer)

    for relative_path, content in files.items():
        await write_card(config.repo_path / relative_path, content, tracer=tracer)
    await commit_as_agent(
        repo_path=config.repo_path,
        agent="architect",
        message=f"docs: audit aval - {', '.join(sorted(files))}",
        files=sorted(files.keys()),
        tracer=tracer,
    )

    usage = result.get("usage", {})
    agent_result = await _make_agent_result(state, config, result, sorted(files.keys()))
    updates: dict = {
        "agent_results": state.agent_results + [agent_result],
        "current_phase": Phase.CLOTURE,
        "total_tokens_sonnet": (
            state.total_tokens_sonnet + usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        ),
    }
    tracer.emit("node_exit", status="success", output_files=sorted(files.keys()))
    return _with_checkpoint(state, updates)


_HANDLERS = {
    Phase.AUDIT_AMONT: _run_audit_amont,
    Phase.AUDIT_STUBS: _run_audit_stubs,
    Phase.AUDIT_AVAL: _run_audit_aval,
}


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node Architecte.

    Args:
        state: État courant du run. state.current_phase détermine le
            comportement (voir description du module ;
            Phase.AUDIT_AMONT/AUDIT_STUBS/AUDIT_AVAL uniquement).
            state.card_root_path et state.agent_cards doivent être
            renseignés selon la phase.

    Returns:
        État mis à jour :
        - En Phase.AUDIT_AMONT : state.architect_brief_path renseigné,
          state.current_phase=Phase.FICHES.
        - En Phase.AUDIT_STUBS, si conforme (voir _parse_audit_decision) :
          state.current_phase=Phase.IMPLEMENTATION,
          state.current_agent_index=0. Si écart détecté :
          state.current_phase=Phase.STUBS, state.current_agent_index
          repositionné sur l'agent fautif (dans la sous-séquence filtrée
          de la phase STUBS), l'agent fautif est ajouté à
          state.failed_agents, sa fiche est annotée.
        - En Phase.AUDIT_AVAL : documentation écrite (potentiellement
          plusieurs fichiers, voir prompts/architect.md), state.current_phase=
          Phase.CLOTURE.
        Dans les trois cas, si should_checkpoint(state) est vrai pour la
        phase courante (config/studio.yml section checkpoints, ou
        state.awaiting_human_validation déjà à True) : state.status=
        RunStatus.WAITING_HUMAN, state.awaiting_human_validation=True, en
        plus de la transition de phase déjà déterminée (le graphe s'arrête
        au prochain routage, voir studio.graph.router).
        Dans tous les cas, un AgentResult est ajouté à state.agent_results.

    Raises:
        RuntimeError: Si l'appel Claude Code CLI échoue, ou si la réponse
            de phase 5 ne respecte pas le format STATUT attendu (voir
            _parse_audit_decision), ou si la réponse de phase 9 ne contient
            aucun bloc de fichier reconnu (voir
            tools.filesystem.parse_agent_file_blocks).
        TimeoutError: Si l'appel dépasse claude_code.timeout_seconds.
        FileNotFoundError: Si card-root.md, une fiche d'agent, ou
            architect-brief.md (phases 5/9) est introuvable.
        KeyError: Si state.current_phase n'est aucune des trois phases
            gérées par ce node.

    Side effects:
        - Appelle tools.claude_code.run_claude_code (modèle
          models.agent_auditor), cwd=config.repo_path (l'Architecte lit le
          code et les stubs lui-même, pas de réinjection intégrale dans le
          prompt).
        - Écrit specs/<specs_dir>/run-<run_id>/architect-brief.md (phase 2)
          ou les fichiers de documentation (phase 9, via
          tools.filesystem.parse_agent_file_blocks) via
          tools.filesystem.write_card.
        - En cas d'écart détecté (phase 5) : annote la fiche de l'agent
          fautif via tools.filesystem.append_feedback.
        - Commit sous l'identité architect-aimazing <architect@aimazing.fr>
          via tools.git.commit_as_agent (phases 2 et 9 — la phase 5 n'écrit
          pas de fichier de périmètre Architecte, seulement une annotation
          sur la fiche d'un autre agent, donc pas de commit ici).
        - Incrémente state.total_tokens_sonnet.

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.AUDIT_STUBS,
        ...     agent_sequence=["back", "front"],
        ...     agent_cards={"back": "specs/run-042/back.md"},
        ...     architect_brief_path="specs/run-042/architect-brief.md",
        ... )
        >>> state = await run(state)
        >>> state.current_phase
        <Phase.IMPLEMENTATION: 6>

    Notes:
        L'Architecte compare systématiquement les diffs produits avec les
        périmètres déclarés dans les fiches (docs/agents.md). Ce node ne
        vérifie pas lui-même le périmètre par du code Python — c'est
        Claude Code (Sonnet) qui fait cette comparaison en lisant le repo,
        via le prompt (prompts/architect.md).

        Un seul écart traité par audit (phase 5) même si plusieurs agents
        en ont un — voir prompts/architect.md, cohérent avec le contrat
        d'origine du stub ("l'agent fautif", singulier).

        Chaque activation est enregistrée via
        studio.metrics.record_agent_result (voir _make_agent_result).
    """
    config = StudioConfig.from_env()
    handler = _HANDLERS.get(state.current_phase)
    if handler is None:
        raise KeyError(
            f"Phase non gérée par le node Architecte : {state.current_phase!r} "
            f"(attendu AUDIT_AMONT, AUDIT_STUBS ou AUDIT_AVAL)"
        )
    tracer = RunTracer.for_run(config, state.run_id).for_agent("architect", state.current_phase)
    tracer.emit("node_enter")
    return await handler(state, config, tracer)
