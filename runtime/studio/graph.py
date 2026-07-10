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
from studio.state import Phase, RunStatus, StudioState

NODE_NAMES = ["pm", "architect", "backend", "frontend", "test", "security", "closer"]

# Mapping agent (tel qu'écrit dans state.agent_sequence par le PM en phase 3,
# voir docs/workflow.md phase 3) -> node du graphe. back-tu/front-tu partagent
# le node de leur agent principal (même identité Git, voir docs/agents.md).
AGENT_TO_NODE = {
    "pm": "pm",
    "architect": "architect",
    "back": "backend",
    "back-tu": "backend",
    "front": "frontend",
    "front-tu": "frontend",
    "test": "test",
    "secu": "security",
}

# Node par défaut pour les phases qui ne dépendent pas de state.agent_sequence.
PHASE_NODE = {
    Phase.RECEPTION: "pm",
    Phase.CADRAGE: "pm",
    Phase.AUDIT_AMONT: "architect",
    Phase.FICHES: "pm",
    Phase.AUDIT_STUBS: "architect",
    Phase.TESTS: "test",
    Phase.SECURITE: "security",
    Phase.AUDIT_AVAL: "architect",
    Phase.CLOTURE: "closer",
}

# Phases où plusieurs agents s'enchaînent selon state.agent_sequence, filtré aux
# rôles concernés par cette phase précise (voir docs/workflow.md phases 4 et 6 —
# back-tu/front-tu/test/secu ne participent pas à la phase 4, contrairement à la 6).
PHASE_AGENT_ROLES = {
    Phase.STUBS: {"back", "front"},
    Phase.IMPLEMENTATION: {"back", "back-tu", "front", "front-tu"},
}

# Checkpoint humain configurable (config/studio.yml section checkpoints) par phase.
PHASE_CHECKPOINT_KEYS = {
    Phase.CADRAGE: "phase_1_cadrage",
    Phase.AUDIT_AMONT: "phase_2_audit_amont",
    Phase.FICHES: "phase_3_fiches",
    Phase.AUDIT_STUBS: "phase_5_audit_stubs",
    Phase.AUDIT_AVAL: "phase_9_audit_aval",
}


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
        roles = PHASE_AGENT_ROLES[state.current_phase]
        phase_sequence = [agent for agent in state.agent_sequence if agent in roles]
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


def should_checkpoint(state: StudioState) -> bool:
    """
    Détermine si un checkpoint humain est nécessaire pour la phase courante.

    Args:
        state: État courant.

    Returns:
        True si un interrupt doit être déclenché avant la prochaine phase :
        soit parce que state.awaiting_human_validation est déjà à True (par
        exemple un trou d'intention détecté en phase 1 — ce cas n'est jamais
        désactivable par la config, voir ADR 0008), soit parce que le
        checkpoint de la phase courante est activé dans
        config/studio.yml (section checkpoints).

    Notes:
        La configuration est chargée via StudioConfig.from_env() (le stub
        d'origine mentionnait `state.config`, qui n'existe pas sur
        StudioState — corrigé après implémentation de config.py, voir
        docs/roadmap.md).
    """
    if state.awaiting_human_validation:
        return True

    checkpoint_key = PHASE_CHECKPOINT_KEYS.get(state.current_phase)
    if checkpoint_key is None:
        return False

    config = StudioConfig.from_env()
    return bool(config.checkpoints.get(checkpoint_key, False))
