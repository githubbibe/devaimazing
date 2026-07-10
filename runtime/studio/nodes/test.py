"""
Node LangGraph - Agent Test.

Agent stateless (ADR 0001) tournant sur Qwen 2.5 7B via Ollama
(models.agents_local). Périmètre : /tests/integration/, /tests/e2e/,
lecture transverse (voir docs/agents.md). Activé en Phase.TESTS (phase 7),
après que Back, Front et leurs sous-rôles -tu ont terminé la phase 6.
"""

from studio.config import StudioConfig
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.filesystem import append_feedback, read_card
from studio.tools.git import commit_as_agent
from studio.tools.ollama import run_ollama


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node Test.

    Args:
        state: État courant du run, avec state.current_phase=Phase.TESTS.
            state.agent_cards["test"] doit référencer les zones d'impact
            identifiées par l'Architecte en phase 2 (architect-brief.md).

    Returns:
        État mis à jour :
        - Si tous les tests (intégration + non-régression) passent :
          state.current_phase=Phase.SECURITE.
        - Si un test de non-régression échoue : state.status=
          RunStatus.WAITING_HUMAN, state.awaiting_human_validation=True,
          state.current_phase inchangée. L'agent Test ne corrige ni le
          test ni le code (voir docs/workflow.md phase 7).
        Un AgentResult est ajouté à state.agent_results dans tous les cas.

    Raises:
        RuntimeError: Si l'appel Ollama échoue après agents.max_iterations
            tentatives.
        TimeoutError: Si l'appel dépasse ollama.timeout_seconds.

    Side effects:
        - Appelle tools.ollama.run_ollama (modèle models.agents_local).
        - Crée des fichiers dans /tests/integration/ et /tests/e2e/.
        - Si un test de non-régression échoue : annote sa propre fiche via
          tools.filesystem.append_feedback avec le nom du test et l'output
          d'erreur brut ; déclenche la notification ntfy
          "❌ [Test] non-régression échouée — <nom test> — <constat brut>"
          (voir docs/workflow.md, section Notifications).
        - Commit sous l'identité test-aimazing <test@aimazing.fr> via
          tools.git.commit_as_agent.
        - Incrémente state.total_tokens_ollama.

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.TESTS,
        ...     agent_cards={"test": "specs/run-042/test.md"},
        ... )
        >>> state = await run(state)
        >>> state.current_phase
        <Phase.SECURITE: 8>

    Notes:
        Le run s'arrête sur un échec de non-régression, sans retry
        automatique — la correction implique potentiellement Back ou
        Front, hors périmètre de l'agent Test (docs/agents.md, Règles de
        périmètre).
    """
    ...
