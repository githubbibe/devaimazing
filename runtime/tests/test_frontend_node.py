"""
Tests du node Front (studio.nodes.frontend) — mêmes garanties que le node
Back (test_backend_node.py), dépendances externes mockées.
"""

import json
from pathlib import Path

import pytest
import yaml

import studio.nodes.frontend as frontend_node
from studio.state import AgentResult, Phase, RunStatus, StudioState


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "project"
    repo.mkdir()
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"agents_local": "qwen2.5:7b-instruct"},
        "ollama": {"base_url": "http://localhost:11434", "timeout_seconds": 120},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
        "agents": {"max_iterations": 3},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    monkeypatch.setenv("DEVAIMAZING_PROJECT", "demo")
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))


def _fake_ollama_result(content: str, tokens_prompt=5, tokens_completion=10, duration_ms=100) -> dict:
    return {
        "content": content,
        "tokens_prompt": tokens_prompt,
        "tokens_completion": tokens_completion,
        "duration_ms": duration_ms,
    }


def _structured_output(files: dict[str, str], blocked_reason: str = "") -> str:
    return json.dumps({
        "files": [{"path": path, "content": content} for path, content in files.items()],
        "blocked_reason": blocked_reason,
    })


FILE_OUTPUT = _structured_output({"frontend/components/LoginForm.tsx": "export const LoginForm = () => null;"})


def _card_metadata(**overrides) -> dict:
    metadata = {
        "files_to_create": [], "files_to_modify": [], "files_forbidden": [],
        "existing_files_to_read": [], "dependencies": [],
    }
    metadata.update(overrides)
    return metadata


async def test_frontend_stub_phase_writes_files_and_commits(monkeypatch: pytest.MonkeyPatch):
    committed = {}

    async def fake_read_card(path):
        return "fiche front"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(repo_path, agent, message, files):
        committed.update(agent=agent, message=message, files=files)
        return "abc123"

    monkeypatch.setattr(frontend_node, "read_card", fake_read_card)
    monkeypatch.setattr(frontend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(frontend_node, "write_card", fake_write_card)
    monkeypatch.setattr(frontend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=1,
        agent_cards={"front": "specs/run-042/front.md"},
        agent_card_metadata={"front": _card_metadata()},
    )

    updates = await frontend_node.run(state)

    result: AgentResult = updates["agent_results"][0]
    assert result.agent == "front"
    assert result.status == "success"
    assert result.output_files == ["frontend/components/LoginForm.tsx"]
    assert committed["agent"] == "front"
    # "front" est en dernière position de ["back", "front"] pour la phase STUBS.
    assert updates["current_phase"] == Phase.AUDIT_STUBS
    assert updates["current_agent_index"] == 0


async def test_frontend_calls_ollama_with_structured_output_schema(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    async def fake_read_card(path):
        return "fiche front"

    async def fake_run_ollama(**kwargs):
        captured["response_format"] = kwargs.get("response_format")
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(frontend_node, "read_card", fake_read_card)
    monkeypatch.setattr(frontend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(frontend_node, "write_card", fake_write_card)
    monkeypatch.setattr(frontend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["front"],
        current_agent_index=0,
        agent_cards={"front": "specs/run-042/front.md"},
        agent_card_metadata={"front": _card_metadata()},
    )

    await frontend_node.run(state)

    assert captured["response_format"] == frontend_node.FILE_OUTPUT_SCHEMA


async def test_frontend_includes_existing_file_content_in_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = tmp_path / "project"
    (repo / "frontend" / "components").mkdir(parents=True)
    (repo / "frontend" / "components" / "LoginForm.tsx").write_text(
        "export const LoginForm = () => <form />;\n", encoding="utf-8"
    )

    async def fake_read_card(path):
        return "Modifier `frontend/components/LoginForm.tsx` pour ajouter un champ."

    captured = {}

    async def fake_run_ollama(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(frontend_node, "read_card", fake_read_card)
    monkeypatch.setattr(frontend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(frontend_node, "write_card", fake_write_card)
    monkeypatch.setattr(frontend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["front"],
        current_agent_index=0,
        agent_cards={"front": "specs/run-042/front.md"},
        agent_card_metadata={
            "front": _card_metadata(
                existing_files_to_read=["frontend/components/LoginForm.tsx"]
            ),
        },
    )

    await frontend_node.run(state)

    assert "export const LoginForm = () => <form />;" in captured["user_prompt"]
    assert "Modifier `frontend/components/LoginForm.tsx`" in captured["user_prompt"]


async def test_frontend_tu_role_uses_test_commit_prefix(monkeypatch: pytest.MonkeyPatch):
    captured_skills = {}
    committed = {}

    async def fake_read_card(path):
        return "fiche front-tu"

    async def fake_inject_skills(base_prompt, skill_names, skills_dir):
        captured_skills["names"] = skill_names
        return base_prompt

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(repo_path, agent, message, files):
        committed.update(agent=agent, message=message)
        return "abc123"

    monkeypatch.setattr(frontend_node, "read_card", fake_read_card)
    monkeypatch.setattr(frontend_node, "inject_skills", fake_inject_skills)
    monkeypatch.setattr(frontend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(frontend_node, "write_card", fake_write_card)
    monkeypatch.setattr(frontend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.IMPLEMENTATION,
        agent_sequence=["front-tu"],
        current_agent_index=0,
        agent_cards={"front-tu": "specs/run-042/front-tu.md"},
        agent_card_metadata={"front-tu": _card_metadata()},
    )

    await frontend_node.run(state)

    assert "non-regression" in captured_skills["names"]
    assert committed["agent"] == "front"
    assert committed["message"].startswith("test:")


async def test_frontend_blocked_reason_appends_feedback_and_waits_for_human(
    monkeypatch: pytest.MonkeyPatch,
):
    feedback_calls = []

    async def fake_read_card(path):
        return "fiche front"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(
            _structured_output({}, blocked_reason="Endpoint backend manquant, je ne peux pas continuer.")
        )

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    monkeypatch.setattr(frontend_node, "read_card", fake_read_card)
    monkeypatch.setattr(frontend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(frontend_node, "append_feedback", fake_append_feedback)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=1,
        agent_cards={"front": "specs/run-042/front.md"},
        agent_card_metadata={"front": _card_metadata()},
    )

    updates = await frontend_node.run(state)

    assert feedback_calls[0][0] == "front"
    assert feedback_calls[0][1] == "Endpoint backend manquant, je ne peux pas continuer."
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert updates["agent_results"][0].status == "feedback_sent"


async def test_frontend_max_iterations_exceeded_fails_without_calling_ollama(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fail_run_ollama(**kwargs):
        raise AssertionError("run_ollama ne doit pas être appelé au-delà de max_iterations")

    monkeypatch.setattr(frontend_node, "run_ollama", fail_run_ollama)

    prior_attempts = [
        AgentResult(agent="front", phase=Phase.STUBS, status="feedback_sent", iteration=i + 1)
        for i in range(3)
    ]
    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=1,
        agent_cards={"front": "specs/run-042/front.md"},
        agent_results=prior_attempts,
    )

    updates = await frontend_node.run(state)

    assert updates["status"] == RunStatus.FAILED
    assert updates["requires_manual_intervention"] is True
    assert "front" in updates["failed_agents"]


async def test_frontend_records_metrics_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    async def fake_read_card(path):
        return "fiche front"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(frontend_node, "read_card", fake_read_card)
    monkeypatch.setattr(frontend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(frontend_node, "write_card", fake_write_card)
    monkeypatch.setattr(frontend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=1,
        agent_cards={"front": "specs/run-042/front.md"},
        agent_card_metadata={"front": _card_metadata()},
    )

    await frontend_node.run(state)

    from studio.metrics import MetricsCollector
    collector = MetricsCollector(tmp_path / "metrics.db")
    summary = await collector.get_run_summary("run-042")
    assert summary["by_agent"]["front"]["task_count"] == 1
