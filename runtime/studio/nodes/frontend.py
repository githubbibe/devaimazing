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

from studio.config import StudioConfig
from studio.state import AgentResult, Phase, StudioState
from studio.tools.filesystem import append_feedback, inject_skills, read_card
from studio.tools.git import commit_as_agent
from studio.tools.ollama import run_ollama


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
        state.current_agent_index est avancé d'une position dans
        state.agent_sequence. state.current_phase passe à
        Phase.AUDIT_STUBS en fin de Phase.STUBS, ou à Phase.TESTS en fin
        de Phase.IMPLEMENTATION (une fois tous les agents de la séquence
        de cette phase terminés).

    Raises:
        RuntimeError: Si l'appel Ollama échoue après agents.max_iterations
            tentatives (config/studio.yml).
        TimeoutError: Si l'appel dépasse ollama.timeout_seconds.
        FileNotFoundError: Si la fiche de l'agent est introuvable.

    Side effects:
        - Appelle tools.ollama.run_ollama (modèle models.agents_local).
        - Crée/modifie des fichiers dans /frontend/ (périmètre déclaré,
          voir docs/agents.md — jamais hors périmètre). Les appels API
          consommés référencent les stubs Back déjà validés.
        - Commit sous l'identité front-aimazing <front@aimazing.fr> à la
          fin de la tâche, via tools.git.commit_as_agent (voir ADR 0007,
          commit_per_task dans config/studio.yml).
        - Incrémente state.total_tokens_ollama.
        - Si un feedback d'un agent en aval a été annoté sur sa fiche lors
          d'une itération précédente, le lit et corrige en conséquence
          (boucle de feedback, voir docs/workflow.md).

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
        fiche est marquée status: failed et le run s'arrête avec une
        notification — pas de retry silencieux au-delà de cette limite
        (docs/workflow.md, section Boucle de feedback).
    """
    ...
