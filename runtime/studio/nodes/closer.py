"""
Node LangGraph - Closer.

Phase.CLOTURE (phase 10), Python pur, zéro appel LLM (voir docs/workflow.md
phase 10, docs/roadmap.md). Les commits ont déjà été réalisés au fil des
phases 4 à 9 (un commit par tâche d'agent terminée, commit_per_task dans
config/studio.yml) ; ce node ne committe plus en bloc.
"""

from studio.config import StudioConfig
from studio.metrics import MetricsCollector
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.filesystem import write_card
from studio.tools.git import merge_run_branch


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node closer.

    Args:
        state: État courant du run, avec state.current_phase=
            Phase.CLOTURE. Tous les artefacts des phases précédentes
            (state.agent_results, state.agent_cards) doivent être présents.

    Returns:
        État final du run : state.status=RunStatus.COMPLETED,
        state.completed_at renseigné. Un AgentResult est ajouté à
        state.agent_results (agent="closer", phase=Phase.CLOTURE).

    Raises:
        RuntimeError: Si le merge de la branche du run échoue (conflit —
            voir tools/git.py::merge_run_branch). Dans ce cas, state.status
            reste RunStatus.WAITING_HUMAN,
            state.requires_manual_intervention=True et
            state.intervention_reason est renseigné plutôt que de
            propager l'exception jusqu'à l'appelant.

    Side effects:
        - Met à jour project-map.md du projet cible (nouveaux
          fichiers/endpoints produits par le run).
        - Met à jour l'historique des runs (utilisé par l'Architecte lors
          des runs suivants pour détecter les doublons).
        - Merge la branche du run vers git.base_branch (develop) via
          tools.git.merge_run_branch, après la dernière validation humaine
          de la phase 9. La branche du run n'est pas supprimée
          (traçabilité et audit, voir tools/git.py).
        - Envoie la notification ntfy "✅ <nom de la feature> terminé".
        - Finalise les métriques du run via
          MetricsCollector.get_run_summary.

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.CLOTURE,
        ...     agent_cards={"back": "specs/run-042/back.md"},
        ... )
        >>> state = await run(state)
        >>> state.status
        <RunStatus.COMPLETED: 'completed'>

    Notes:
        Ce node n'appelle jamais tools.claude_code.run_claude_code ni
        tools.ollama.run_ollama — toute la logique est déterministe (voir
        docs/roadmap.md, étape 1 : "closer en phase 10 est Python pur,
        sans appel LLM").
    """
    ...
