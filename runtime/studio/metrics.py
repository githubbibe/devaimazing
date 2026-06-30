"""
Collecte et export des métriques devaimazing.

Stockage : SQLite (metrics.db)
Export : Prometheus (prometheus_client)
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from prometheus_client import Counter, Histogram, Gauge


# Métriques Prometheus
TOKENS_TOTAL = Counter(
    "devaimazing_tokens_total",
    "Total tokens consommés",
    ["agent", "model", "type"]  # type: prompt | completion
)

TASK_DURATION = Histogram(
    "devaimazing_task_duration_seconds",
    "Durée des tâches agents",
    ["agent", "phase"],
    buckets=[1, 5, 10, 30, 60, 120, 300]
)

AGENT_ITERATIONS = Counter(
    "devaimazing_agent_iterations_total",
    "Nombre d'itérations par agent (renvois inclus)",
    ["agent", "status"]  # status: success | error | feedback_sent
)

RAM_USAGE = Gauge(
    "devaimazing_ram_usage_gb",
    "RAM utilisée sur le Mac mini pendant un run"
)

OLLAMA_LATENCY = Histogram(
    "devaimazing_ollama_latency_seconds",
    "Latence de réponse Ollama",
    ["model"],
    buckets=[1, 2, 5, 10, 30, 60, 120]
)


@dataclass
class TaskMetrics:
    """Métriques d'une activation d'agent."""
    task_id: str
    run_id: str
    card_id: str
    agent: str
    phase: int
    model: str
    tokens_prompt: int
    tokens_completion: int
    llm_duration_ms: int
    total_duration_ms: int
    claude_code_calls: int
    status: str
    iteration: int
    created_at: datetime


class MetricsCollector:
    """
    Collecte les métriques par tâche, fiche et run.
    
    Persiste dans metrics.db et exporte vers Prometheus.
    """

    def __init__(self, db_path: Path):
        """
        Args:
            db_path: Chemin vers metrics.db.
        """
        ...

    async def record_task(self, metrics: TaskMetrics) -> None:
        """
        Enregistre les métriques d'une tâche dans metrics.db et Prometheus.

        Args:
            metrics: Métriques de la tâche complétée.

        Side effects:
            Insert dans metrics.db, met à jour les compteurs Prometheus.
        """
        ...

    async def get_run_summary(self, run_id: str) -> dict:
        """
        Retourne le résumé agrégé d'un run.

        Args:
            run_id: Identifiant du run.

        Returns:
            Dictionnaire avec tokens par type, durée, répartition par agent et phase.

        Raises:
            ValueError: Si run_id inconnu.
        """
        ...

    async def record_system_metrics(self) -> None:
        """
        Collecte les métriques système (RAM, CPU, GPU, latence Ollama).
        Appelée périodiquement pendant un run.

        Side effects:
            Met à jour les gauges Prometheus système.
        """
        ...
