"""
Tests du node Test (studio.nodes.test) — dépendances externes mockées,
sauf la commande de test elle-même qui utilise de vrais sous-process
(shell simples : true/false/python -c) pour vérifier le câblage subprocess
réel de _run_test_command.
"""

from pathlib import Path

import pytest
import yaml

import studio.nodes.test as test_node
from studio.state import AgentResult, Phase, RunStatus, StudioState


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    return repo


def _configure_env(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, test_command=None):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"agents_local": "qwen2.5:7b-instruct"},
        "ollama": {"base_url": "http://localhost:11434", "timeout_seconds": 120},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
        "agents": {"max_iterations": 3},
    })
    project_data = {"repo_path": str(repo)}
    if test_command is not None:
        project_data["test"] = {"command": test_command}
    _write_yaml(config_dir / "projects" / "demo.yml", project_data)
    monkeypatch.setenv("DEVAIMAZING_PROJECT", "demo")
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))


def _fake_ollama_result(content: str) -> dict:
    return {"content": content, "tokens_prompt": 5, "tokens_completion": 10, "duration_ms": 100}


FILE_BLOCK = (
    '<<<DEVAIMAZING_FILE path="tests/integration/test_login_flow.py">>>\n'
    'def test_login():\n'
    '    assert True\n'
    '<<<DEVAIMAZING_END>>>'
)


def _base_state(repo: Path) -> StudioState:
    return StudioState(
        run_id="run-042",
        current_phase=Phase.TESTS,
        agent_sequence=["test"],
        current_agent_index=0,
        agent_cards={"test": "specs/run-042/test.md"},
    )


async def test_test_node_no_command_configured_writes_and_advances(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)

    async def fake_read_card(path):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)

    updates = await test_node.run(_base_state(repo))

    result: AgentResult = updates["agent_results"][0]
    assert result.status == "success"
    assert updates["current_phase"] == Phase.SECURITE
    assert updates["current_agent_index"] == 0


async def test_test_node_includes_existing_file_content_in_prompt(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "main.py").write_text(
        "def complete_todo(todo_id: int):\n    ...\n", encoding="utf-8"
    )

    async def fake_read_card(path):
        return "Écrire un test d'intégration pour le handler dans `backend/main.py`."

    captured = {}

    async def fake_run_ollama(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)

    await test_node.run(_base_state(repo))

    assert "def complete_todo(todo_id: int):" in captured["user_prompt"]
    assert "Écrire un test d'intégration" in captured["user_prompt"]


async def test_test_node_command_passes_advances_to_securite(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command="python3 -c \"exit(0)\"")

    async def fake_read_card(path):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)

    updates = await test_node.run(_base_state(repo))

    assert updates["agent_results"][0].status == "success"
    assert updates["current_phase"] == Phase.SECURITE


async def test_test_node_command_fails_appends_feedback_and_waits_for_human(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(
        tmp_path, repo, monkeypatch,
        test_command="python3 -c \"import sys; print('boom'); sys.exit(1)\"",
    )

    feedback_calls = []

    async def fake_read_card(path):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(test_node, "append_feedback", fake_append_feedback)

    updates = await test_node.run(_base_state(repo))

    assert updates["agent_results"][0].status == "error"
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert "boom" in feedback_calls[0][1]
    assert "current_phase" not in updates


async def test_test_node_no_file_blocks_appends_feedback(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)

    feedback_calls = []

    async def fake_read_card(path):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result("Impossible de tester sans les endpoints backend.")

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "append_feedback", fake_append_feedback)

    updates = await test_node.run(_base_state(repo))

    assert updates["agent_results"][0].status == "feedback_sent"
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert feedback_calls[0][0] == "test"


async def test_test_node_max_iterations_exceeded_fails_without_calling_ollama(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)

    async def fail_run_ollama(**kwargs):
        raise AssertionError("run_ollama ne doit pas être appelé au-delà de max_iterations")

    monkeypatch.setattr(test_node, "run_ollama", fail_run_ollama)

    prior_attempts = [
        AgentResult(agent="test", phase=Phase.TESTS, status="error", iteration=i + 1)
        for i in range(3)
    ]
    state = _base_state(repo)
    state.agent_results = prior_attempts

    updates = await test_node.run(state)

    assert updates["status"] == RunStatus.FAILED
    assert updates["requires_manual_intervention"] is True
    assert "test" in updates["failed_agents"]


async def test_test_node_records_metrics_on_success(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)

    async def fake_read_card(path):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)

    await test_node.run(_base_state(repo))

    from studio.metrics import MetricsCollector
    collector = MetricsCollector(tmp_path / "metrics.db")
    summary = await collector.get_run_summary("run-042")
    assert summary["by_agent"]["test"]["task_count"] == 1


async def test_run_test_command_reports_success_and_failure(tmp_path: Path):
    ok, output = await test_node._run_test_command("python3 -c \"exit(0)\"", tmp_path)
    assert ok is True

    ok, output = await test_node._run_test_command(
        "python3 -c \"import sys; print('nope'); sys.exit(1)\"", tmp_path
    )
    assert ok is False
    assert "nope" in output


async def test_run_test_command_tolerates_braces_unrelated_to_target_dir(tmp_path: Path):
    # Régression : un usage naïf de str.format() sur la commande casserait
    # dès que la commande contient d'autres accolades que {target_dir}
    # (ex. un dict literal Python) — voir test_security_node.py, même bug
    # trouvé sur _run_sast_tool.
    command = "python3 -c \"import json; print(json.dumps({'ok': True}))\""
    ok, output = await test_node._run_test_command(command, tmp_path)
    assert ok is True
    assert '"ok": true' in output
