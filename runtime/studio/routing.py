"""
Constantes et helpers de routage partagés entre graph.py et nodes/*.py.

Isolés dans ce module (plutôt que dans graph.py) pour éviter un import
circulaire : graph.py importe studio.nodes (pour enregistrer les nodes du
graphe), donc studio.nodes ne peut pas importer studio.graph en retour.
"""

from studio.config import StudioConfig
from studio.state import Phase, StudioState

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

# Phase suivante une fois la sous-séquence d'une phase à agents multiples épuisée.
NEXT_PHASE_AFTER = {
    Phase.STUBS: Phase.AUDIT_STUBS,
    Phase.IMPLEMENTATION: Phase.TESTS,
}

# Checkpoint humain configurable (config/studio.yml section checkpoints) par phase.
PHASE_CHECKPOINT_KEYS = {
    Phase.CADRAGE: "phase_1_cadrage",
    Phase.AUDIT_AMONT: "phase_2_audit_amont",
    Phase.FICHES: "phase_3_fiches",
    Phase.AUDIT_STUBS: "phase_5_audit_stubs",
    Phase.AUDIT_AVAL: "phase_9_audit_aval",
}


def phase_agent_sequence(state: StudioState) -> list[str]:
    """
    Sous-séquence de state.agent_sequence filtrée aux rôles de la phase
    courante (voir PHASE_AGENT_ROLES). Liste vide si la phase courante n'est
    pas une phase à agents multiples (STUBS, IMPLEMENTATION).
    """
    roles = PHASE_AGENT_ROLES.get(state.current_phase)
    if roles is None:
        return []
    return [agent for agent in state.agent_sequence if agent in roles]


def is_last_agent_of_phase(state: StudioState) -> bool:
    """
    True si l'agent à state.current_agent_index est le dernier de la
    sous-séquence filtrée de la phase courante (voir phase_agent_sequence).
    """
    sequence = phase_agent_sequence(state)
    return state.current_agent_index >= len(sequence) - 1


def agent_iteration_count(state: StudioState, agent: str) -> int:
    """
    Nombre de tentatives déjà enregistrées pour cet agent à la phase
    courante (avant la tentative en cours) — compte les entrées de
    state.agent_results dont agent et phase correspondent.
    """
    return sum(1 for r in state.agent_results if r.agent == agent and r.phase == state.current_phase)


def max_iterations_exceeded(state: StudioState, config: StudioConfig, agent: str) -> bool:
    """
    True si une nouvelle tentative de `agent` à la phase courante
    dépasserait agents.max_iterations (config/studio.yml, défaut 3).

    Args:
        state: État courant, avant la tentative envisagée.
        config: Configuration du run.
        agent: Nom de l'agent (tel qu'écrit dans state.agent_sequence).

    Returns:
        True si agent_iteration_count(state, agent) a déjà atteint
        max_iterations — la tentative en cours serait donc la
        (max_iterations + 1)-ième, à refuser (voir docs/workflow.md,
        section Boucle de feedback : "Si N échoue après 3 itérations, la
        fiche est marquée status: failed").
    """
    max_iterations = config.get("agents", {}).get("max_iterations", 3)
    return agent_iteration_count(state, agent) >= max_iterations


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

        Déplacée de graph.py vers routing.py : les nodes (architect.py,
        pm.py) doivent aussi pouvoir l'appeler, et studio.nodes ne peut pas
        importer studio.graph (import circulaire, graph.py importe
        studio.nodes).
    """
    if state.awaiting_human_validation:
        return True

    checkpoint_key = PHASE_CHECKPOINT_KEYS.get(state.current_phase)
    if checkpoint_key is None:
        return False

    config = StudioConfig.from_env()
    return bool(config.checkpoints.get(checkpoint_key, False))
