"""
Collecte et export des métriques devaimazing.

Stockage : SQLite (metrics.db)
Export : Prometheus (prometheus_client)
"""

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from prometheus_client import Counter, Histogram, Gauge

from studio.config import StudioConfig
from studio.state import AgentResult, StudioState


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

_CREATE_TASKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    phase INTEGER NOT NULL,
    model TEXT NOT NULL,
    tokens_prompt INTEGER NOT NULL,
    tokens_completion INTEGER NOT NULL,
    llm_duration_ms INTEGER NOT NULL,
    total_duration_ms INTEGER NOT NULL,
    claude_code_calls INTEGER NOT NULL,
    status TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
"""


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

        Side effects:
            Crée le répertoire parent de db_path si nécessaire, et la table
            `tasks` si elle n'existe pas déjà (schéma initialisé de façon
            synchrone — opération rapide, unique par processus).
        """
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(_CREATE_TASKS_TABLE_SQL)
            conn.commit()

    async def record_task(self, metrics: TaskMetrics) -> None:
        """
        Enregistre les métriques d'une tâche dans metrics.db et Prometheus.

        Args:
            metrics: Métriques de la tâche complétée.

        Side effects:
            Insert (ou remplace, si task_id déjà présent) dans metrics.db,
            met à jour les compteurs Prometheus (TOKENS_TOTAL, TASK_DURATION,
            AGENT_ITERATIONS).
        """
        async with aiosqlite.connect(str(self._db_path)) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    task_id, run_id, card_id, agent, phase, model,
                    tokens_prompt, tokens_completion, llm_duration_ms,
                    total_duration_ms, claude_code_calls, status, iteration,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metrics.task_id, metrics.run_id, metrics.card_id, metrics.agent,
                    metrics.phase, metrics.model, metrics.tokens_prompt,
                    metrics.tokens_completion, metrics.llm_duration_ms,
                    metrics.total_duration_ms, metrics.claude_code_calls,
                    metrics.status, metrics.iteration, metrics.created_at.isoformat(),
                ),
            )
            await conn.commit()

        TOKENS_TOTAL.labels(agent=metrics.agent, model=metrics.model, type="prompt").inc(
            metrics.tokens_prompt
        )
        TOKENS_TOTAL.labels(agent=metrics.agent, model=metrics.model, type="completion").inc(
            metrics.tokens_completion
        )
        TASK_DURATION.labels(agent=metrics.agent, phase=str(metrics.phase)).observe(
            metrics.total_duration_ms / 1000
        )
        AGENT_ITERATIONS.labels(agent=metrics.agent, status=metrics.status).inc()

    async def get_run_summary(self, run_id: str) -> dict:
        """
        Retourne le résumé agrégé d'un run.

        Args:
            run_id: Identifiant du run.

        Returns:
            Dictionnaire avec : run_id, task_count, tokens_prompt,
            tokens_completion, total_duration_ms (agrégats globaux),
            by_agent et by_phase (mêmes agrégats, ventilés par agent et par
            phase respectivement).

        Raises:
            ValueError: Si run_id inconnu (aucune tâche enregistrée).
        """
        async with aiosqlite.connect(str(self._db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM tasks WHERE run_id = ?", (run_id,))
            rows = await cursor.fetchall()

        if not rows:
            raise ValueError(f"Run inconnu : {run_id!r}")

        def _empty_bucket() -> dict:
            return {"tokens_prompt": 0, "tokens_completion": 0, "duration_ms": 0, "task_count": 0}

        by_agent: dict[str, dict] = {}
        by_phase: dict[int, dict] = {}
        total = _empty_bucket()

        for row in rows:
            for bucket_map, key in ((by_agent, row["agent"]), (by_phase, row["phase"])):
                bucket = bucket_map.setdefault(key, _empty_bucket())
                bucket["tokens_prompt"] += row["tokens_prompt"]
                bucket["tokens_completion"] += row["tokens_completion"]
                bucket["duration_ms"] += row["total_duration_ms"]
                bucket["task_count"] += 1
            total["tokens_prompt"] += row["tokens_prompt"]
            total["tokens_completion"] += row["tokens_completion"]
            total["duration_ms"] += row["total_duration_ms"]
            total["task_count"] += 1

        return {
            "run_id": run_id,
            "task_count": total["task_count"],
            "tokens_prompt": total["tokens_prompt"],
            "tokens_completion": total["tokens_completion"],
            "total_duration_ms": total["duration_ms"],
            "by_agent": by_agent,
            "by_phase": by_phase,
        }

    async def record_system_metrics(self) -> None:
        """
        Collecte les métriques système (RAM, CPU, GPU, latence Ollama).
        Appelée périodiquement pendant un run.

        Side effects:
            Met à jour les gauges Prometheus système (RAM_USAGE).

        Notes:
            Mesure limitée à la RSS (Resident Set Size) du process
            devaimazing courant, via le module stdlib `resource` — pas la
            RAM système globale du Mac mini, et pas de métrique CPU/GPU.
            `psutil` (qui permettrait un monitoring système complet)
            n'est pas une dépendance du projet ; à ajouter explicitement si
            un monitoring plus complet est nécessaire (voir
            docs/roadmap.md). OLLAMA_LATENCY n'est pas mise à jour ici :
            elle relève de tools/ollama.py, pas de cette collecte
            périodique.
        """
        import resource

        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS (RUSAGE_SELF.ru_maxrss en octets) vs Linux (en kilooctets).
        ram_gb = max_rss / (1024 ** 3) if sys.platform == "darwin" else max_rss / (1024 ** 2)
        RAM_USAGE.set(ram_gb)


async def record_agent_result(
    config: StudioConfig,
    state: StudioState,
    agent_result: AgentResult,
    model: str,
    claude_code_calls: int = 0,
) -> None:
    """
    Construit un TaskMetrics depuis un AgentResult déjà produit par un node
    et l'enregistre — évite de dupliquer cette construction dans chaque
    node producteur/auditeur.

    Args:
        config: Configuration du run (fournit metrics_db_path).
        state: État courant AVANT la mise à jour retournée par le node
            (utilisé pour state.run_id et state.agent_cards).
        agent_result: Résultat déjà construit par le node (agent, phase,
            status, tokens, duration, iteration, output_files).
        model: Identifiant du modèle utilisé pour cette tâche.
        claude_code_calls: Nombre d'appels Claude Code CLI effectués pour
            cette tâche (0 pour les agents sur Ollama).

    Side effects:
        Insert dans metrics.db, met à jour les compteurs Prometheus (voir
        MetricsCollector.record_task).

    Notes:
        task_id est construit à partir de run_id/agent/phase/iteration —
        stable et unique pour une même tentative, ce qui permet un
        INSERT OR REPLACE idempotent si un node est rejoué (voir
        MetricsCollector.record_task).
    """
    collector = MetricsCollector(config.metrics_db_path)
    task_id = f"{state.run_id}-{agent_result.agent}-{agent_result.phase.value}-{agent_result.iteration}"
    await collector.record_task(TaskMetrics(
        task_id=task_id,
        run_id=state.run_id,
        card_id=state.agent_cards.get(agent_result.agent, agent_result.agent),
        agent=agent_result.agent,
        phase=agent_result.phase.value,
        model=model,
        tokens_prompt=agent_result.tokens_prompt,
        tokens_completion=agent_result.tokens_completion,
        llm_duration_ms=agent_result.duration_ms,
        total_duration_ms=agent_result.duration_ms,
        claude_code_calls=claude_code_calls,
        status=agent_result.status,
        iteration=agent_result.iteration,
        created_at=datetime.now(timezone.utc),
    ))
