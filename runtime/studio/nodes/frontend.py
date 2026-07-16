"""
Node LangGraph - Agent Front.

Agent stateless (ADR 0001) tournant sur Qwen 2.5 7B via Ollama
(models.agents_local, voir docs/llm-strategy.md). Périmètre : /frontend/
(voir docs/agents.md). Ce node couvre deux activations selon
state.current_phase :

- Phase.STUBS (phase 4) : crée les composants et interfaces stub du
  périmètre Front (signatures, contrats — voir skills/stub-first.md).
  Aucune logique métier à ce stade.
- Phase.IMPLEMENTATION (phase 6) : implémente les composants selon les
  stubs validés par l'Architecte en phase 5.

Le sous-rôle Front-tu (tests unitaires frontend, périmètre
/tests/unit/frontend/) partage ce node et l'identité Git de Front (voir
docs/agents.md) ; il est distingué par l'entrée correspondante dans
state.agent_sequence, pas par un node séparé.
"""

from pathlib import Path

from studio.config import StudioConfig
from studio.metrics import record_agent_result
from studio.routing import (
    NEXT_PHASE_AFTER,
    agent_iteration_count,
    is_last_agent_of_phase,
    max_iterations_exceeded,
)
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.filesystem import (
    append_feedback,
    inject_skills,
    parse_structured_file_output,
    read_card,
    read_files,
    write_card,
)
from studio.tools.git import commit_as_agent
from studio.tools.ollama import FILE_OUTPUT_SCHEMA, run_ollama
from studio.tools.tracer import RunTracer

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _DEVAIMAZING_ROOT / "prompts" / "frontend.md"
_SKILLS_DIR = _DEVAIMAZING_ROOT / "skills"
_SKILL_NAMES = ["stub-first", "error-handling", "logging-conventions"]
_TU_EXTRA_SKILLS = ["non-regression"]

_GIT_IDENTITY_AGENT = "front"  # front-tu commit sous l'identité front (docs/agents.md)


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node Front.

    Args:
        state: État courant du run. state.current_phase détermine le
            comportement (Phase.STUBS ou Phase.IMPLEMENTATION).
            state.agent_cards["front"] (ou "front-tu") doit être renseigné.

    Returns:
        État mis à jour : un AgentResult est ajouté à state.agent_results
        avec output_files listant les fichiers créés/modifiés.
        state.current_agent_index est avancé d'une position dans la
        sous-séquence filtrée de la phase (voir studio.routing). Si l'agent
        courant est le dernier de cette sous-séquence, state.current_phase
        passe à Phase.AUDIT_STUBS (fin de Phase.STUBS) ou Phase.TESTS (fin
        de Phase.IMPLEMENTATION) et l'index repart à 0.

        Si l'agent signale un blocage (champ "blocked_reason" non vide dans
        sa sortie structurée — voir tools.ollama.FILE_OUTPUT_SCHEMA), ou si
        "files" est vide : le blocked_reason (ou le texte brut si la sortie
        est malformée malgré la contrainte de schéma) est ajouté à la
        section Feedback de sa propre fiche, l'AgentResult a
        status="feedback_sent", et state.status=RunStatus.WAITING_HUMAN /
        state.awaiting_human_validation=True.

        Si l'agent a déjà atteint agents.max_iterations tentatives pour
        cette phase (voir studio.routing.max_iterations_exceeded) : aucun
        appel Ollama n'est fait, state.status=RunStatus.FAILED,
        state.requires_manual_intervention=True,
        state.intervention_reason renseigné, l'agent est ajouté à
        state.failed_agents.

    Raises:
        RuntimeError: Si l'appel Ollama échoue après agents.max_iterations
            tentatives (config/studio.yml) — propagé tel quel depuis
            tools.ollama.run_ollama (ExternalServiceError).
        TimeoutError: Si l'appel dépasse ollama.timeout_seconds.
        FileNotFoundError: Si la fiche de l'agent est introuvable.

    Side effects:
        - Appelle tools.ollama.run_ollama (modèle models.agents_local), avec
          response_format=tools.ollama.FILE_OUTPUT_SCHEMA (sortie contrainte
          par grammaire — voir docs/roadmap.md, chantier "sortie structurée",
          2026-07-11).
        - Crée/modifie des fichiers dans /frontend/ (périmètre déclaré,
          voir docs/agents.md — jamais hors périmètre : le node écrit
          exactement les chemins renvoyés par l'agent, la vérification de
          périmètre est le rôle de l'Architecte en phase 5/9). Les appels
          API consommés référencent les stubs Back déjà validés.
        - Commit sous l'identité front-aimazing <front@aimazing.fr> à la
          fin de la tâche, via tools.git.commit_as_agent (voir ADR 0007,
          commit_per_task dans config/studio.yml).
        - Incrémente state.total_tokens_ollama.
        - Si la fiche contient déjà une annotation de feedback (renvoi
          après écart détecté par l'Architecte), elle est naturellement
          incluse dans le prompt utilisateur (lecture intégrale de la
          fiche) : pas de traitement spécial nécessaire côté node.

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.STUBS,
        ...     agent_sequence=["back", "back-tu", "front", "front-tu", "test", "secu"],
        ...     agent_cards={"front": "specs/run-042/front.md"},
        ... )
        >>> state = await run(state)
        >>> state.agent_results[-1].agent
        'front'

    Notes:
        Après agents.max_iterations échecs consécutifs (3 par défaut), la
        fiche est marquée status: failed et le run s'arrête — pas de retry
        silencieux au-delà de cette limite (docs/workflow.md, section
        Boucle de feedback ; limite appliquée via
        studio.routing.max_iterations_exceeded, voir docs/roadmap.md pour
        la décision). La notification ntfy correspondante n'est pas
        câblée (pas d'outil de notification implémenté).

        Chaque tentative (succès, feedback_sent, ou l'échec final) est
        enregistrée via studio.metrics.record_agent_result.
    """
    config = StudioConfig.from_env()
    role = state.agent_sequence[state.current_agent_index]
    tracer = RunTracer.for_run(config, state.run_id).for_agent(role, state.current_phase)
    tracer.emit("node_enter", card=state.agent_cards.get(role))

    if max_iterations_exceeded(state, config, role):
        max_iterations = config.get("agents", {}).get("max_iterations", 3)
        tracer.emit("node_exit", status="failed", reason="max_iterations_exceeded")
        return {
            "status": RunStatus.FAILED,
            "requires_manual_intervention": True,
            "intervention_reason": (
                f"Agent {role!r} a atteint la limite de {max_iterations} itérations "
                f"en phase {state.current_phase.name} sans succès."
            ),
            "failed_agents": (
                state.failed_agents if role in state.failed_agents
                else state.failed_agents + [role]
            ),
        }

    card_path = config.repo_path / state.agent_cards[role]
    card_content = await read_card(card_path, tracer=tracer)
    existing_files_context = await read_files(
        config.repo_path, state.agent_card_metadata[role]["existing_files_to_read"], tracer=tracer
    )
    user_prompt = (
        f"{existing_files_context}\n\n---\n\n{card_content}" if existing_files_context else card_content
    )

    skill_names = list(_SKILL_NAMES)
    if role == "front-tu":
        skill_names += _TU_EXTRA_SKILLS

    system_prompt = await inject_skills(
        base_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        skill_names=skill_names,
        skills_dir=_SKILLS_DIR,
    )

    ollama_config = config.get("ollama", {})
    result = await run_ollama(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=config.models["agents_local"],
        base_url=config.ollama_base_url,
        timeout_seconds=ollama_config.get("timeout_seconds", 120),
        num_ctx=ollama_config.get("num_ctx", 16384),
        response_format=FILE_OUTPUT_SCHEMA,
        tracer=tracer,
    )

    iteration = agent_iteration_count(state, role) + 1

    try:
        files, blocked_reason = parse_structured_file_output(result["content"], tracer=tracer)
    except ValueError:
        files, blocked_reason = {}, ""

    if blocked_reason or not files:
        await append_feedback(
            card_path, agent_source=role, feedback=blocked_reason or result["content"]
        )
        agent_result = AgentResult(
            agent=role,
            phase=state.current_phase,
            status="feedback_sent",
            iteration=iteration,
            tokens_prompt=result["tokens_prompt"],
            tokens_completion=result["tokens_completion"],
            duration_ms=result["duration_ms"],
        )
        await record_agent_result(config, state, agent_result, model=config.models["agents_local"])
        tracer.emit("node_exit", status="feedback_sent")
        return {
            "agent_results": state.agent_results + [agent_result],
            "status": RunStatus.WAITING_HUMAN,
            "awaiting_human_validation": True,
            "total_tokens_ollama": state.total_tokens_ollama + result["tokens_prompt"] + result["tokens_completion"],
        }

    for relative_path, content in files.items():
        await write_card(config.repo_path / relative_path, content, tracer=tracer)

    is_implementation = state.current_phase == Phase.IMPLEMENTATION
    if role == "front-tu":
        phase_label = "unit tests" if is_implementation else "unit tests (stub)"
    else:
        phase_label = "implementation" if is_implementation else "stub"
    commit_prefix = "test" if role == "front-tu" else "feat"
    message = f"{commit_prefix}: {phase_label} - {', '.join(sorted(files))}"

    await commit_as_agent(
        repo_path=config.repo_path,
        agent=_GIT_IDENTITY_AGENT,
        message=message,
        files=sorted(files.keys()),
        tracer=tracer,
    )

    agent_result = AgentResult(
        agent=role,
        phase=state.current_phase,
        status="success",
        output_files=sorted(files.keys()),
        iteration=iteration,
        tokens_prompt=result["tokens_prompt"],
        tokens_completion=result["tokens_completion"],
        duration_ms=result["duration_ms"],
    )
    await record_agent_result(config, state, agent_result, model=config.models["agents_local"])
    tracer.emit("node_exit", status="success", output_files=sorted(files.keys()))

    updates: dict = {
        "agent_results": state.agent_results + [agent_result],
        "total_tokens_ollama": state.total_tokens_ollama + result["tokens_prompt"] + result["tokens_completion"],
    }

    if is_last_agent_of_phase(state):
        updates["current_phase"] = NEXT_PHASE_AFTER[state.current_phase]
        updates["current_agent_index"] = 0
    else:
        updates["current_agent_index"] = state.current_agent_index + 1

    return updates
