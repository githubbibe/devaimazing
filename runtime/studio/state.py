"""
État partagé du graphe LangGraph devaimazing.

Seul le PM est stateful. Les autres agents sont stateless et
reçoivent uniquement leur prompt + skills + fiche à chaque activation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class RunStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class Phase(int, Enum):
    RECEPTION = 0
    CADRAGE = 1
    AUDIT_AMONT = 2
    FICHES = 3
    STUBS = 4
    AUDIT_STUBS = 5
    IMPLEMENTATION = 6
    TESTS = 7
    SECURITE = 8
    AUDIT_AVAL = 9
    CLOTURE = 10


@dataclass
class AgentResult:
    """Résultat d'une activation d'agent."""
    agent: str
    phase: Phase
    status: str  # success | error | feedback_sent
    output_files: list[str] = field(default_factory=list)
    feedback: Optional[str] = None
    iteration: int = 1
    tokens_prompt: int = 0
    tokens_completion: int = 0
    duration_ms: int = 0


@dataclass
class StudioState:
    """
    État global du run devaimazing.
    
    Persisté via le checkpointer SQLite LangGraph.
    Seul le PM lit et écrit cet état entre les phases.
    Les agents stateless reçoivent uniquement ce dont ils ont besoin
    via leurs fiches .md, pas via cet état directement.
    """
    # Identifiants
    run_id: str = ""
    project_name: str = ""
    objective_raw: str = ""

    # Phase courante
    current_phase: Phase = Phase.RECEPTION
    status: RunStatus = RunStatus.PENDING

    # Séquence définie par le PM en phase 3
    # Ex: ["back", "back-tu", "front", "front-tu", "test", "secu"]
    agent_sequence: list[str] = field(default_factory=list)
    current_agent_index: int = 0

    # Fiches créées (chemin relatif dans le repo projet)
    card_root_path: Optional[str] = None
    architect_brief_path: Optional[str] = None
    agent_cards: dict[str, str] = field(default_factory=dict)  # agent -> chemin fiche

    # Branche Git du run, créée par le PM en phase 3 (studio.tools.git.create_run_branch).
    # Nécessaire à la phase 10 (closer) pour le merge — pas recalculable après coup, le
    # nom de branche contient un hash basé sur le timestamp de création (voir ADR 0007).
    branch_name: Optional[str] = None

    # Résultats des agents
    agent_results: list[AgentResult] = field(default_factory=list)

    # Checkpoints humains
    awaiting_human_validation: bool = False
    human_validation_phase: Optional[Phase] = None

    # Métriques du run
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_tokens_opus: int = 0
    total_tokens_sonnet: int = 0
    total_tokens_ollama: int = 0
    total_tokens_fallback: int = 0

    # Erreurs et fallbacks
    failed_agents: list[str] = field(default_factory=list)
    requires_manual_intervention: bool = False
    intervention_reason: Optional[str] = None
