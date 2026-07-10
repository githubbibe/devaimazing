"""
Graphe LangGraph devaimazing.

Définit le graphe d'états du studio : nodes (agents) et edges (transitions).
Le graphe est séquentiel avec des branches conditionnelles pour :
- Les checkpoints humains (interrupt_before)
- Les boucles de feedback (renvoi à l'agent précédent)
- Le routage dynamique (séquence définie par le PM en phase 3)
"""

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from studio.config import StudioConfig
from studio.nodes import architect, backend, closer, frontend, pm, security, test
from studio.routing import (
    AGENT_TO_NODE,
    PHASE_AGENT_ROLES,
    PHASE_NODE,
    phase_agent_sequence,
    should_checkpoint,
)
from studio.state import RunStatus, StudioState

# Ré-exporté pour compatibilité : should_checkpoint vit dans studio.routing
# (les nodes doivent aussi pouvoir l'appeler, voir sa docstring).
__all__ = ["build_graph", "router", "should_checkpoint"]

NODE_NAMES = ["pm", "architect", "backend", "frontend", "test", "security", "closer"]


async def build_graph(config: StudioConfig) -> CompiledStateGraph:
    """
    Construit et compile le graphe LangGraph du studio.

    Args:
        config: Instance de StudioConfig avec la configuration du run.

    Returns:
        Graphe compilé avec checkpointer SQLite (schéma initialisé au premier
        appel si state_db_path n'existait pas encore).

    Raises:
        OSError: Si le répertoire parent de state_db_path n'est pas
            accessible en écriture.

    Side effects:
        Crée le répertoire parent de state_db_path si nécessaire. Ouvre une
        connexion SQLite conservée pour la durée de vie du graphe compilé —
        cette fonction ne la ferme pas, à la charge de l'appelant (cli.py).

    Notes:
        Le graphe est séquentiel. Les transitions dynamiques (séquence
        agents) sont gérées par la fonction router() qui lit l'état courant.

        Rendue async, contrairement à la signature d'origine du stub :
        AsyncSqliteSaver (langgraph-checkpoint-sqlite) exige une connexion
        aiosqlite ouverte via `await`, impossible dans une fonction
        synchrone. Vérifié contre l'API réelle du paquet (voir ADR 0003).
    """
    graph = StateGraph(StudioState)

    graph.add_node("pm", pm.run)
    graph.add_node("architect", architect.run)
    graph.add_node("backend", backend.run)
    graph.add_node("frontend", frontend.run)
    graph.add_node("test", test.run)
    graph.add_node("security", security.run)
    graph.add_node("closer", closer.run)

    graph.add_edge(START, "pm")

    routing_map = {name: name for name in NODE_NAMES}
    routing_map[END] = END
    for name in NODE_NAMES:
        graph.add_conditional_edges(name, router, routing_map)

    config.state_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(config.state_db_path))
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()

    return graph.compile(checkpointer=checkpointer)


def router(state: StudioState) -> str:
    """
    Fonction de routing dynamique.

    Détermine le prochain node selon l'état courant du run.
    Appelée après chaque node pour décider de la transition.

    Args:
        state: État courant.

    Returns:
        Nom du prochain node, ou END si le run est terminé ou en attente
        d'une validation humaine (state.status == WAITING_HUMAN — le graphe
        s'arrête là, la reprise se fait via `devaimazing resume`, pas par un
        interrupt LangGraph natif, voir docstring de build_graph).

    Raises:
        ValueError: Si state.agent_sequence est incohérent avec la phase
            courante (current_agent_index hors bornes, ou agent absent de
            AGENT_TO_NODE). Un état malformé signale un bug ailleurs dans le
            runtime (le node qui a produit cet état) — ne pas l'absorber
            silencieusement en routant vers END.
    """
    if state.status in (RunStatus.WAITING_HUMAN, RunStatus.FAILED, RunStatus.COMPLETED):
        return END

    if state.current_phase in PHASE_AGENT_ROLES:
        phase_sequence = phase_agent_sequence(state)
        if state.current_agent_index >= len(phase_sequence):
            raise ValueError(
                f"current_agent_index ({state.current_agent_index}) hors bornes pour "
                f"la phase {state.current_phase.name} (séquence filtrée : {phase_sequence})"
            )
        agent = phase_sequence[state.current_agent_index]
        if agent not in AGENT_TO_NODE:
            raise ValueError(f"Agent inconnu dans agent_sequence : {agent!r}")
        return AGENT_TO_NODE[agent]

    return PHASE_NODE.get(state.current_phase, END)
