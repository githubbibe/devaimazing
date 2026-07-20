"""
Graphe LangGraph devaimazing.

Définit le graphe d'états du studio : nodes (agents) et edges (transitions).
Le graphe est séquentiel avec des branches conditionnelles pour :
- Les checkpoints humains (interrupt_before)
- Les boucles de feedback (renvoi à l'agent précédent)
- Le routage dynamique (séquence définie par le PM en phase 3)
"""

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from studio.config import StudioConfig
from studio.nodes import architect, backend, closer, frontend, pm, security, test
from studio.routing import (
    AGENT_TO_NODE,
    PHASE_AGENT_ROLES,
    PHASE_NODE,
    should_checkpoint,
)
from studio.state import AgentResult, Phase, RunStatus, StudioState

# Types studio.state stockés tels quels dans le state du graphe (donc dans le
# checkpoint LangGraph) : sans les déclarer ici, leur désérialisation lève un
# warning (et sera bloquée par défaut dans une future version de
# langgraph-checkpoint-sqlite) — voir docs/roadmap.md, 2026-07-15.
_ALLOWED_MSGPACK_MODULES = [Phase, RunStatus, AgentResult]

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

        Le checkpointer est construit avec un JsonPlusSerializer déclarant
        explicitement _ALLOWED_MSGPACK_MODULES (Phase, RunStatus, AgentResult
        — les types studio.state stockés tels quels dans StudioState) : sans
        ça, LangGraph journalise un warning de désérialisation à chaque
        resume/retry et bloquera par défaut dans une future version (voir
        docs/roadmap.md, 2026-07-15).
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
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    checkpointer = AsyncSqliteSaver(conn, serde=serde)
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
            courante (current_agent_index hors bornes, agent à cet index
            n'appartenant pas aux rôles de la phase courante — voir
            PHASE_AGENT_ROLES —, ou agent absent de AGENT_TO_NODE). Un état
            malformé signale un bug ailleurs dans le runtime (le node qui a
            produit cet état) — ne pas l'absorber silencieusement en
            routant vers END.

    Notes:
        Indexe directement state.agent_sequence (la séquence complète),
        jamais une sous-liste filtrée par phase — un bug réel en run
        (2026-07-20, voir docs/roadmap.md et PHASE_AGENT_ROLES dans
        routing.py) venait d'un décalage entre une sous-liste filtrée à 2
        éléments utilisée ici pour l'indexation et la séquence complète à
        6 éléments utilisée par backend.py/frontend.py pour résoudre le
        rôle réel : back-tu (index 1 dans la séquence complète) se
        retrouvait routé vers le node "frontend" (index 1 de la sous-liste
        filtrée ["back", "front"]).
    """
    if state.status in (RunStatus.WAITING_HUMAN, RunStatus.FAILED, RunStatus.COMPLETED):
        return END

    if state.current_phase in PHASE_AGENT_ROLES:
        if state.current_agent_index >= len(state.agent_sequence):
            raise ValueError(
                f"current_agent_index ({state.current_agent_index}) hors bornes pour "
                f"state.agent_sequence (longueur {len(state.agent_sequence)})"
            )
        agent = state.agent_sequence[state.current_agent_index]
        if agent not in PHASE_AGENT_ROLES[state.current_phase]:
            raise ValueError(
                f"Agent {agent!r} (index {state.current_agent_index}) n'appartient pas "
                f"aux rôles de la phase {state.current_phase.name} "
                f"({sorted(PHASE_AGENT_ROLES[state.current_phase])})"
            )
        if agent not in AGENT_TO_NODE:
            raise ValueError(f"Agent inconnu dans agent_sequence : {agent!r}")
        return AGENT_TO_NODE[agent]

    return PHASE_NODE.get(state.current_phase, END)
