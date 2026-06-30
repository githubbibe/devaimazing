"""
Graphe LangGraph devaimazing.

Définit le graphe d'états du studio : nodes (agents) et edges (transitions).
Le graphe est séquentiel avec des branches conditionnelles pour :
- Les checkpoints humains (interrupt_before)
- Les boucles de feedback (renvoi à l'agent précédent)
- Le routage dynamique (séquence définie par le PM en phase 3)
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from studio.state import StudioState, Phase
from studio.nodes import pm, architect, backend, frontend, test, security, closer


def build_graph(config) -> StateGraph:
    """
    Construit et compile le graphe LangGraph du studio.

    Args:
        config: Instance de StudioConfig avec la configuration du run.

    Returns:
        Graphe compilé avec checkpointer SQLite.

    Notes:
        Le graphe est séquentiel. Les transitions dynamiques (séquence agents)
        sont gérées par la fonction router() qui lit l'état courant.
    """
    ...


def router(state: StudioState) -> str:
    """
    Fonction de routing dynamique.

    Détermine le prochain node selon l'état courant du run.
    Appelée après chaque node pour décider de la transition.

    Args:
        state: État courant.

    Returns:
        Nom du prochain node, ou END si le run est terminé.
    """
    ...


def should_checkpoint(state: StudioState) -> bool:
    """
    Détermine si un checkpoint humain est nécessaire pour la phase courante.

    Lit la configuration des checkpoints depuis state.config.

    Args:
        state: État courant.

    Returns:
        True si un interrupt doit être déclenché avant la prochaine phase.
    """
    ...
