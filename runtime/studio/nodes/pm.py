"""
Node LangGraph - Agent pm.

Reçoit l'état du run, exécute la tâche de l'agent,
retourne l'état mis à jour.
"""

from studio.state import StudioState


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node pm.

    Args:
        state: État courant du run.

    Returns:
        État mis à jour après exécution de l'agent.
    """
    ...
