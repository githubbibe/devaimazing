"""
Tests du node closer (studio.nodes.closer).

merge_run_branch est mocké (les tests git réels sont dans test_git.py).
MetricsCollector tourne réellement (rapide, déjà testé séparément).
"""

from pathlib import Path

import pytest
import yaml

import studio.nodes.closer as closer_node
from studio.state import AgentResult, Phase, RunStatus, StudioState


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    return repo


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "git": {"base_branch": "develop"},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
        "notifications": {"ntfy": {"server_url": "https://ntfy.sh", "topic": "<PLACEHOLDER_TOPIC>"}},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    monkeypatch.setenv("DEVAIMAZING_PROJECT", "demo")
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))


def _base_state(**overrides) -> StudioState:
    defaults = dict(
        run_id="run-042",
        project_name="demo",
        objective_raw="ajouter un panier",
        current_phase=Phase.CLOTURE,
        branch_name="studio/ajout-panier-a3f9c",
        agent_results=[
            AgentResult(agent="back", phase=Phase.STUBS, status="success", output_files=["backend/a.py"]),
        ],
    )
    defaults.update(overrides)
    return StudioState(**defaults)


def test_insert_table_rows_inserts_after_separator():
    content = (
        "## Carte des fichiers\n\n"
        "| Chemin | Rôle | Agent | Run | Contraintes |\n"
        "|---|---|---|---|---|\n"
        "| | | | | |\n\n"
        "## Autre section\n"
    )

    result = closer_node._insert_table_rows(
        content, "## Carte des fichiers", ["| backend/a.py | - | back | run-042 | - |"]
    )

    assert "| backend/a.py | - | back | run-042 | - |" in result
    assert result.index("| backend/a.py") < result.index("## Autre section")


async def test_update_project_map_creates_from_template(repo: Path, monkeypatch: pytest.MonkeyPatch):
    from studio.config import StudioConfig
    config = StudioConfig.from_env()
    state = _base_state()

    await closer_node._update_project_map(config, state)

    project_map_path = repo / "specs" / "project-map.md"
    assert project_map_path.is_file()
    content = project_map_path.read_text(encoding="utf-8")
    assert "backend/a.py" in content
    assert "run-042" in content
    assert "ajouter un panier" in content


async def test_update_project_map_appends_to_existing(repo: Path):
    from studio.config import StudioConfig
    config = StudioConfig.from_env()
    project_map_path = repo / "specs" / "project-map.md"
    project_map_path.parent.mkdir(parents=True)
    project_map_path.write_text(
        closer_node._PROJECT_MAP_TEMPLATE_PATH.read_text(encoding="utf-8").replace(
            "{{PROJECT_NAME}}", "demo"
        ),
        encoding="utf-8",
    )

    await closer_node._update_project_map(config, _base_state(run_id="run-001"))
    await closer_node._update_project_map(config, _base_state(run_id="run-002"))

    content = project_map_path.read_text(encoding="utf-8")
    assert "run-001" in content
    assert "run-002" in content


async def test_notify_skips_placeholder_topic(monkeypatch: pytest.MonkeyPatch, repo: Path):
    from studio.config import StudioConfig
    config = StudioConfig.from_env()

    calls = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content):
            calls.append(url)

    monkeypatch.setattr(closer_node.httpx, "AsyncClient", lambda **kwargs: _FakeClient())

    await closer_node._notify(config, "test")

    assert calls == []


async def test_run_success_merges_and_completes(monkeypatch: pytest.MonkeyPatch, repo: Path):
    async def fake_merge_run_branch(repo_path, branch_name, target_branch="develop"):
        return "mergehash123"

    monkeypatch.setattr(closer_node, "merge_run_branch", fake_merge_run_branch)

    updates = await closer_node.run(_base_state())

    assert updates["status"] == RunStatus.COMPLETED
    assert "completed_at" in updates
    assert updates["agent_results"][-1].agent == "closer"
    assert (repo / "specs" / "project-map.md").is_file()


async def test_run_merge_conflict_waits_for_human(monkeypatch: pytest.MonkeyPatch, repo: Path):
    async def fake_merge_run_branch(repo_path, branch_name, target_branch="develop"):
        raise RuntimeError("conflit de merge")

    monkeypatch.setattr(closer_node, "merge_run_branch", fake_merge_run_branch)

    updates = await closer_node.run(_base_state())

    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["requires_manual_intervention"] is True
    assert "conflit de merge" in updates["intervention_reason"]
    # project-map.md n'est pas mis à jour si le merge échoue.
    assert not (repo / "specs" / "project-map.md").is_file()


async def test_run_missing_branch_name_raises_value_error(repo: Path):
    state = _base_state(branch_name=None)

    with pytest.raises(ValueError):
        await closer_node.run(state)


async def test_run_records_own_metrics_and_includes_summary_in_notification(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
):
    from studio.metrics import MetricsCollector, TaskMetrics
    from datetime import datetime, timezone

    async def fake_merge_run_branch(repo_path, branch_name, target_branch="develop"):
        return "mergehash123"

    monkeypatch.setattr(closer_node, "merge_run_branch", fake_merge_run_branch)

    # Simule une tâche déjà enregistrée par un node producteur en amont.
    collector = MetricsCollector(tmp_path / "metrics.db")
    await collector.record_task(TaskMetrics(
        task_id="t1", run_id="run-042", card_id="c1", agent="back", phase=4,
        model="qwen2.5:7b-instruct", tokens_prompt=10, tokens_completion=5,
        llm_duration_ms=100, total_duration_ms=100, claude_code_calls=0,
        status="success", iteration=1, created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    ))

    notified = []

    async def fake_notify(config, message):
        notified.append(message)

    monkeypatch.setattr(closer_node, "_notify", fake_notify)

    await closer_node.run(_base_state())

    assert "1 tâches" in notified[0]
    assert "15 tokens" in notified[0]

    summary = await collector.get_run_summary("run-042")
    assert summary["by_agent"]["closer"]["task_count"] == 1
