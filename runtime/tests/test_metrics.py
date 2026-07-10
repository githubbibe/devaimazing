"""
Tests de la collecte de métriques devaimazing (metrics.db, pas les
compteurs Prometheus globaux — état de process partagé, pas testé finement
ici, seulement que record_task ne lève pas en les mettant à jour).
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from studio.metrics import MetricsCollector, TaskMetrics


def _make_task(**overrides) -> TaskMetrics:
    defaults = dict(
        task_id="task-1",
        run_id="run-042",
        card_id="card-1",
        agent="back",
        phase=4,
        model="qwen2.5:7b-instruct",
        tokens_prompt=100,
        tokens_completion=50,
        llm_duration_ms=2000,
        total_duration_ms=2500,
        claude_code_calls=0,
        status="success",
        iteration=1,
        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return TaskMetrics(**defaults)


def test_metrics_collector_creates_db_file(tmp_path: Path):
    db_path = tmp_path / "sub" / "metrics.db"

    MetricsCollector(db_path)

    assert db_path.is_file()


async def test_record_task_and_get_run_summary_aggregates(tmp_path: Path):
    collector = MetricsCollector(tmp_path / "metrics.db")

    await collector.record_task(_make_task(
        task_id="task-1", agent="back", phase=4, tokens_prompt=100, tokens_completion=50,
        total_duration_ms=2500,
    ))
    await collector.record_task(_make_task(
        task_id="task-2", agent="front", phase=4, tokens_prompt=200, tokens_completion=80,
        total_duration_ms=3000,
    ))
    await collector.record_task(_make_task(
        task_id="task-3", agent="back", phase=6, tokens_prompt=50, tokens_completion=20,
        total_duration_ms=1000,
    ))

    summary = await collector.get_run_summary("run-042")

    assert summary["task_count"] == 3
    assert summary["tokens_prompt"] == 350
    assert summary["tokens_completion"] == 150
    assert summary["total_duration_ms"] == 6500

    assert summary["by_agent"]["back"]["task_count"] == 2
    assert summary["by_agent"]["back"]["tokens_prompt"] == 150
    assert summary["by_agent"]["front"]["task_count"] == 1
    assert summary["by_agent"]["front"]["tokens_prompt"] == 200

    assert summary["by_phase"][4]["task_count"] == 2
    assert summary["by_phase"][6]["task_count"] == 1


async def test_get_run_summary_unknown_run_raises_value_error(tmp_path: Path):
    collector = MetricsCollector(tmp_path / "metrics.db")

    with pytest.raises(ValueError):
        await collector.get_run_summary("run-inconnu")


async def test_record_task_same_task_id_replaces_previous(tmp_path: Path):
    collector = MetricsCollector(tmp_path / "metrics.db")

    await collector.record_task(_make_task(task_id="task-1", status="feedback_sent", iteration=1))
    await collector.record_task(_make_task(task_id="task-1", status="success", iteration=2))

    summary = await collector.get_run_summary("run-042")

    # Un seul enregistrement pour task-1 (remplacé, pas dupliqué).
    assert summary["task_count"] == 1


async def test_record_system_metrics_does_not_raise(tmp_path: Path):
    collector = MetricsCollector(tmp_path / "metrics.db")

    await collector.record_system_metrics()
