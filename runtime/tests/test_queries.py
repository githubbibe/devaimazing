"""
Tests de studio.tools.queries.

build_graph est mocké (comme dans test_cli.py, qui exerce déjà exactement ce
même chemin aget_state/checkpointer) — le graphe LangGraph lui-même n'est
pas ce qu'on veut vérifier ici, seulement l'extraction faite à partir de son
état.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import studio.tools.queries as queries_module
from studio.state import AgentResult, Phase, RunStatus
from studio.tools.queries import (
    build_progression_summary,
    get_run_progression,
    get_run_snapshot,
    list_projects,
    parse_run_history_table,
)


class _FakeSnapshot:
    def __init__(self, values: dict):
        self.values = values


def _fake_checkpointer(closed: list) -> SimpleNamespace:
    async def _close():
        closed.append(True)

    return SimpleNamespace(conn=SimpleNamespace(close=_close))


def _mock_build_graph(monkeypatch: pytest.MonkeyPatch, state: dict, closed: list):
    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state) if state else {})

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, checkpointer=_fake_checkpointer(closed)
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(queries_module, "build_graph", fake_build_graph)


async def test_get_run_snapshot_not_found(monkeypatch: pytest.MonkeyPatch):
    closed: list = []
    _mock_build_graph(monkeypatch, state={}, closed=closed)

    result = await get_run_snapshot(config=object(), run_id="run-042")

    assert result == {"found": False}
    assert closed == [True]  # checkpointer fermé même si le run n'existe pas


async def test_get_run_snapshot_found(monkeypatch: pytest.MonkeyPatch):
    state = {
        "current_phase": Phase.STUBS,
        "status": RunStatus.IN_PROGRESS,
        "agent_sequence": ["back", "back-tu"],
        "current_agent_index": 1,
    }
    closed: list = []
    _mock_build_graph(monkeypatch, state=state, closed=closed)

    result = await get_run_snapshot(config=object(), run_id="run-042")

    assert result["found"] is True
    assert result["current_agent"] == "back-tu"
    assert closed == [True]


async def test_get_run_snapshot_unknown_agent_index(monkeypatch: pytest.MonkeyPatch):
    state = {
        "current_phase": Phase.STUBS,
        "status": RunStatus.IN_PROGRESS,
        "agent_sequence": ["back"],
        "current_agent_index": 5,
    }
    _mock_build_graph(monkeypatch, state=state, closed=[])

    result = await get_run_snapshot(config=object(), run_id="run-042")

    assert result["current_agent"] is None


async def test_get_run_progression_includes_last_result_and_intervention(
    monkeypatch: pytest.MonkeyPatch,
):
    state = {
        "current_phase": Phase.STUBS,
        "status": RunStatus.WAITING_HUMAN,
        "agent_sequence": ["back"],
        "current_agent_index": 0,
        "agent_results": [
            AgentResult(agent="back", phase=Phase.STUBS, status="feedback_sent", iteration=2),
        ],
        "requires_manual_intervention": True,
        "intervention_reason": "3 itérations épuisées",
    }
    _mock_build_graph(monkeypatch, state=state, closed=[])

    result = await get_run_progression(config=object(), run_id="run-042")

    assert result["found"] is True
    assert result["last_result"] == {"agent": "back", "status": "feedback_sent", "iteration": 2}
    assert result["requires_manual_intervention"] is True
    assert result["intervention_reason"] == "3 itérations épuisées"


async def test_get_run_progression_not_found(monkeypatch: pytest.MonkeyPatch):
    _mock_build_graph(monkeypatch, state={}, closed=[])

    result = await get_run_progression(config=object(), run_id="run-042")

    assert result == {"found": False}


def test_build_progression_summary_no_agent_results():
    state = {
        "current_phase": Phase.CADRAGE,
        "status": RunStatus.PENDING,
        "agent_sequence": [],
        "current_agent_index": 0,
        "agent_results": [],
    }

    summary = build_progression_summary(state)

    assert summary["last_result"] is None
    assert summary["requires_manual_intervention"] is False


def test_parse_run_history_table():
    content = (
        "## Historique des runs\n\n"
        "| Run ID | Date | Objectif | Statut | Fichiers créés | Fichiers modifiés |\n"
        "|---|---|---|---|---|---|\n"
        "| run-001 | 2026-07-10 | x | completed | 3 | - |\n"
        "\n## Points de vigilance\n"
    )

    rows = parse_run_history_table(content)

    assert rows == [["run-001", "2026-07-10", "x", "completed", "3", "-"]]


def test_parse_run_history_table_no_section():
    assert parse_run_history_table("# Rien ici\n") == []


async def test_list_projects_sorted(tmp_path: Path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "webaimazing.yml").write_text("name: webaimazing\n", encoding="utf-8")
    (projects_dir / "demo.yml").write_text("name: demo\n", encoding="utf-8")

    names = await list_projects(tmp_path)

    assert names == ["demo", "webaimazing"]


async def test_list_projects_missing_dir(tmp_path: Path):
    names = await list_projects(tmp_path / "inexistant")

    assert names == []
