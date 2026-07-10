"""
Tests du node Front (studio.nodes.frontend) — mêmes garanties que le node
Back (test_backend_node.py), dépendances externes mockées.
"""

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


FILE_BLOCK = (
    '<<<DEVAIMAZING_FILE path="frontend/components/LoginForm.tsx">>>\n'
    'export const LoginForm = () => null;\n'
    '<<<DEVAIMAZING_END>>>'
)


async def test_frontend_stub_phase_writes_files_and_commits(monkeypatch: pytest.MonkeyPatch):
    committed = {}

    async def fake_read_card(path):
        return "fiche front"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

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


async def test_frontend_tu_role_uses_test_commit_prefix(monkeypatch: pytest.MonkeyPatch):
    captured_skills = {}
    committed = {}

    async def fake_read_card(path):
        return "fiche front-tu"

    async def fake_inject_skills(base_prompt, skill_names, skills_dir):
        captured_skills["names"] = skill_names
        return base_prompt

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

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
    )

    await frontend_node.run(state)

    assert "non-regression" in captured_skills["names"]
    assert committed["agent"] == "front"
    assert committed["message"].startswith("test:")


async def test_frontend_no_file_blocks_appends_feedback_and_waits_for_human(monkeypatch: pytest.MonkeyPatch):
    feedback_calls = []

    async def fake_read_card(path):
        return "fiche front"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result("Endpoint backend manquant, je ne peux pas continuer.")

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
    )

    updates = await frontend_node.run(state)

    assert feedback_calls[0][0] == "front"
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert updates["agent_results"][0].status == "feedback_sent"
