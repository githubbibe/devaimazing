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

# Phases où plusieurs agents s'enchaînent selon state.agent_sequence — le rôle
# à state.current_agent_index doit appartenir à cet ensemble pour la phase
# courante (voir router() dans graph.py et is_last_agent_of_phase ci-dessous).
# back-tu/front-tu participent bien à la phase 4 (écrivent les tests unitaires
# en même temps que les stubs de code, voir docs/agents.md) — élargi le
# 2026-07-20 après un bug réel trouvé en run (voir docs/roadmap.md) : cet
# ensemble ne contenait que {"back", "front"}, alors que back-tu/front-tu sont
# bel et bien programmés par le PM dans cette phase ; router()/
# is_last_agent_of_phase indexaient une sous-liste filtrée à 2 éléments avec
# un compteur qui progresse sur la séquence complète à 6 éléments — décalage
# qui envoyait le node "backend" (rôle back-tu) tourner sous le node
# "frontend" (identité git, prompt système et skills de front, pas de back).
PHASE_AGENT_ROLES = {
    Phase.STUBS: {"back", "back-tu", "front", "front-tu"},
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


def is_last_agent_of_phase(state: StudioState) -> bool:
    """
    True si l'agent suivant state.current_agent_index dans
    state.agent_sequence (la séquence COMPLÈTE — pas de sous-liste
    filtrée, voir PHASE_AGENT_ROLES ci-dessus pour l'historique du bug que
    ça a causé) n'appartient plus aux rôles de la phase courante, ou si
    current_agent_index est déjà le dernier élément de la séquence.
    """
    roles = PHASE_AGENT_ROLES.get(state.current_phase, set())
    next_index = state.current_agent_index + 1
    if next_index >= len(state.agent_sequence):
        return True
    return state.agent_sequence[next_index] not in roles


def agent_iteration_count(state: StudioState, agent: str) -> int:
    """
    Nombre de tentatives déjà enregistrées pour cet agent à la phase
    courante (avant la tentative en cours) — compte les entrées de
    state.agent_results dont agent et phase correspondent.

    Pour les phases à agents multiples (STUBS, IMPLEMENTATION — voir
    PHASE_AGENT_ROLES), un succès remet ce compteur à zéro : sans ça, un
    agent déjà réussi peut se retrouver bloqué à tort quand la phase est
    rejouée à cause d'un AUTRE agent du même groupe désigné fautif par
    l'Architecte (AUDIT_STUBS/AUDIT_AVAL) — gap trouvé en run le
    2026-07-19 sur back-tu (voir docs/roadmap.md) : back-tu réussit,
    l'audit désigne back comme fautif, back corrige, mais back-tu était
    déjà à agents.max_iterations (2 feedback_sent + 1 success cumulés
    depuis le début du run) et échouait immédiatement sans même retenter.

    Pour les phases à agent unique (SECURITE, TESTS, ...), le compteur
    reste cumulatif sur tout le run sans remise à zéro : un succès n'y
    signifie pas "cet agent a fini son tour et peut être rejoué sans
    risque" (ex. Sécu peut ré-émettre un rapport "success" à chaque
    reprise humaine tant que les findings bloquants ne sont pas corrigés
    — la boucle de garde-fou doit continuer à compter ces succès répétés).
    """
    resets_on_success = state.current_phase in PHASE_AGENT_ROLES
    count = 0
    for r in state.agent_results:
        if r.agent != agent or r.phase != state.current_phase:
            continue
        count = 0 if (resets_on_success and r.status == "success") else count + 1
    return count


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
