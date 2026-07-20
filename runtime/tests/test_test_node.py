"""
Tests du node Test (studio.nodes.test) — dépendances externes mockées,
sauf la commande de test elle-même qui utilise de vrais sous-process
(shell simples : true/false/python -c) pour vérifier le câblage subprocess
réel de _run_test_command.
"""

import json
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


def _structured_output(files: dict[str, str], blocked_reason: str = "") -> str:
    return json.dumps({
        "files": [{"path": path, "content": content} for path, content in files.items()],
        "blocked_reason": blocked_reason,
    })


FILE_OUTPUT = _structured_output({"tests/integration/test_login_flow.py": "def test_login():\n    assert True"})


def _card_metadata(**overrides) -> dict:
    metadata = {
        "files_to_create": [], "files_to_modify": [], "files_forbidden": [],
        "existing_files_to_read": [], "dependencies": [],
    }
    metadata.update(overrides)
    return metadata


def _base_state(repo: Path, existing_files_to_read: list[str] | None = None) -> StudioState:
    return StudioState(
        run_id="run-042",
        current_phase=Phase.TESTS,
        agent_sequence=["test"],
        current_agent_index=0,
        agent_cards={"test": "specs/run-042/test.md"},
        agent_card_metadata={
            "test": _card_metadata(existing_files_to_read=existing_files_to_read or []),
        },
    )


async def test_test_node_no_command_configured_writes_and_advances(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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


async def test_test_node_calls_ollama_with_structured_output_schema(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)
    captured = {}

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        captured["response_format"] = kwargs.get("response_format")
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)

    await test_node.run(_base_state(repo))

    assert captured["response_format"] == test_node.FILE_OUTPUT_SCHEMA


async def test_test_node_includes_existing_file_content_in_prompt(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "main.py").write_text(
        "def complete_todo(todo_id: int):\n    ...\n", encoding="utf-8"
    )

    async def fake_read_card(path, tracer=None):
        return "Écrire un test d'intégration pour le handler dans `backend/main.py`."

    captured = {}

    async def fake_run_ollama(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)

    await test_node.run(_base_state(repo, existing_files_to_read=["backend/main.py"]))

    assert "def complete_todo(todo_id: int):" in captured["user_prompt"]
    assert "Écrire un test d'intégration" in captured["user_prompt"]


async def test_test_node_command_passes_advances_to_securite(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command="python3 -c \"exit(0)\"")

    committed = {"called": False}

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(**kwargs):
        committed["called"] = True
        return "abc123"

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)

    updates = await test_node.run(_base_state(repo))

    assert updates["agent_results"][0].status == "success"
    assert updates["current_phase"] == Phase.SECURITE
    assert committed["called"] is True


async def test_test_node_command_fails_does_not_commit(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Régression 2026-07-20 (voir docs/roadmap.md) : le commit ne doit avoir
    lieu qu'après un test_command réussi — un test qui échoue à l'exécution
    ne doit jamais atterrir en historique Git, découvert seulement après
    coup au moment de l'échec.
    """
    _configure_env(
        tmp_path, repo, monkeypatch,
        test_command="python3 -c \"import sys; print('boom'); sys.exit(1)\"",
    )

    committed = {"called": False}

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(**kwargs):
        committed["called"] = True
        return "abc123"

    async def fake_append_feedback(card_path, agent_source, feedback):
        pass

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(test_node, "append_feedback", fake_append_feedback)

    updates = await test_node.run(_base_state(repo))

    assert updates["agent_results"][0].status == "error"
    assert committed["called"] is False


async def test_test_node_command_fails_appends_feedback_and_waits_for_human(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(
        tmp_path, repo, monkeypatch,
        test_command="python3 -c \"import sys; print('boom'); sys.exit(1)\"",
    )

    feedback_calls = []

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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


def _traceback_command(implicated_file: Path) -> str:
    """Commande shell dont la sortie imite une traceback pytest --tb=native minimale."""
    return (
        "python3 -c \"import sys; "
        f"print('File \\\"{implicated_file}\\\", line 1, in test_x'); "
        "sys.exit(1)\""
    )


async def test_test_node_command_fails_escalates_to_owning_producer(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Régression 2026-07-20 (voir docs/roadmap.md) : un échec de test_command
    dont la sortie (--tb=native) implique un fichier du périmètre Back doit
    router vers un redo ciblé de back (retry_scope, current_phase, current_
    agent_index), pas s'arrêter sur WAITING_HUMAN — feedback sur LA FICHE DE
    BACK, pas celle de Test.
    """
    backend_file = repo / "backend" / "main.py"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("def create_todo():\n    pass\n", encoding="utf-8")
    _configure_env(tmp_path, repo, monkeypatch, test_command=_traceback_command(backend_file))

    feedback_calls = []

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(**kwargs):
        raise AssertionError("commit_as_agent ne doit pas être appelé sur un échec")

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((str(card_path), agent_source))

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(test_node, "append_feedback", fake_append_feedback)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.TESTS,
        agent_sequence=["back", "test"],
        current_agent_index=1,
        agent_cards={"back": "specs/run-042/back.md", "test": "specs/run-042/test.md"},
        agent_card_metadata={"test": _card_metadata()},
    )

    updates = await test_node.run(state)

    assert updates.get("status") != RunStatus.WAITING_HUMAN
    assert updates["current_phase"] == Phase.IMPLEMENTATION
    assert updates["current_agent_index"] == 0
    assert updates["failed_agents"] == ["back"]
    assert updates["retry_scope"]["back"]
    assert len(feedback_calls) == 1
    assert feedback_calls[0][1] == "test"
    assert feedback_calls[0][0].endswith("specs/run-042/back.md")


async def test_test_node_command_fails_escalates_with_flat_backend_layout(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Même scénario que ci-dessus, mais avec structure.backend_dir="" (layout
    plat, ex. todo-list2 — voir config/projects/todo-list2.yml, 2026-07-20) :
    _owning_producer_agent doit reconnaître un fichier à la racine du repo
    comme relevant de back quand backend_dir est vide, pas seulement quand
    il est sous un sous-dossier backend/.
    """
    backend_file = repo / "main.py"
    backend_file.write_text("def create_todo():\n    pass\n", encoding="utf-8")
    _configure_env(tmp_path, repo, monkeypatch, test_command=_traceback_command(backend_file))

    config_dir = tmp_path / "config"
    project_data = yaml.safe_load((config_dir / "projects" / "demo.yml").read_text())
    project_data["structure"] = {"backend_dir": ""}
    _write_yaml(config_dir / "projects" / "demo.yml", project_data)

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(**kwargs):
        raise AssertionError("commit_as_agent ne doit pas être appelé sur un échec")

    async def fake_append_feedback(card_path, agent_source, feedback):
        pass

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(test_node, "append_feedback", fake_append_feedback)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.TESTS,
        agent_sequence=["back", "test"],
        current_agent_index=1,
        agent_cards={"back": "specs/run-042/back.md", "test": "specs/run-042/test.md"},
        agent_card_metadata={"test": _card_metadata()},
    )

    updates = await test_node.run(state)

    assert updates["current_phase"] == Phase.IMPLEMENTATION
    assert updates["failed_agents"] == ["back"]


async def test_test_node_command_fails_in_test_file_does_not_escalate(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Si la traceback n'implique QUE le fichier de test lui-même (bug de test,
    pas de code produit), pas d'escalade automatique — Test ne se corrige
    jamais lui-même (voir docstring de run()), comportement WAITING_HUMAN
    existant préservé.
    """
    test_file = repo / "tests" / "integration" / "test_login_flow.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_login():\n    assert True\n", encoding="utf-8")
    _configure_env(tmp_path, repo, monkeypatch, test_command=_traceback_command(test_file))

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(**kwargs):
        raise AssertionError("commit_as_agent ne doit pas être appelé sur un échec")

    async def fake_append_feedback(card_path, agent_source, feedback):
        pass

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "write_card", fake_write_card)
    monkeypatch.setattr(test_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(test_node, "append_feedback", fake_append_feedback)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.TESTS,
        agent_sequence=["back", "test"],
        current_agent_index=1,
        agent_cards={"back": "specs/run-042/back.md", "test": "specs/run-042/test.md"},
        agent_card_metadata={"test": _card_metadata()},
    )

    updates = await test_node.run(state)

    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert "current_phase" not in updates
    assert "retry_scope" not in updates


async def test_test_node_blocked_reason_appends_feedback(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_env(tmp_path, repo, monkeypatch, test_command=None)

    feedback_calls = []

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(
            _structured_output({}, blocked_reason="Impossible de tester sans les endpoints backend.")
        )

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    monkeypatch.setattr(test_node, "read_card", fake_read_card)
    monkeypatch.setattr(test_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(test_node, "append_feedback", fake_append_feedback)

    updates = await test_node.run(_base_state(repo))

    assert updates["agent_results"][0].status == "feedback_sent"
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert feedback_calls[0][0] == "test"
    assert feedback_calls[0][1] == "Impossible de tester sans les endpoints backend."


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

    async def fake_read_card(path, tracer=None):
        return "fiche test"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
