"""
Tests du node Sécu (studio.nodes.security).

_run_sast_tool est testée séparément avec de vrais sous-process (python -c
qui imprime du JSON), le reste du node est testé avec _run_sast_tool et
run_claude_code mockés.
"""

from pathlib import Path

import pytest
import yaml

import studio.nodes.security as security_node
from studio.state import Phase, RunStatus, StudioState


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
        "models": {"agent_auditor": "claude-sonnet-4-6"},
        "sast": {
            "enabled": True,
            "tools": [
                {"name": "bandit", "command": "echo bandit"},
                {"name": "semgrep", "command": "echo semgrep"},
            ],
            "fail_on_severity": "HIGH",
        },
        "claude_code": {"timeout_seconds": 300, "output_format": "json"},
        "structure": {"specs_dir": "specs/"},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    monkeypatch.setenv("DEVAIMAZING_PROJECT", "demo")
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))


def _base_state() -> StudioState:
    return StudioState(
        run_id="run-042",
        current_phase=Phase.SECURITE,
        agent_sequence=["secu"],
        current_agent_index=0,
        agent_cards={"secu": "specs/run-042/secu.md"},
    )


def _fake_claude_result(content="# Rapport sécurité\n\nAucun problème."):
    return {
        "content": content,
        "usage": {"input_tokens": 100, "output_tokens": 200},
        "duration_ms": 1500,
    }


async def test_security_no_blocking_findings_advances_to_audit_aval(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    async def fake_read_card(path):
        return "fiche secu"

    async def fake_run_sast_tool(command, target_dir):
        return {"results": [{"issue_severity": "LOW"}]}

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result()

    written = {}

    async def fake_write_card(path, content):
        written[str(path)] = content

    committed = {}

    async def fake_commit_as_agent(repo_path, agent, message, files):
        committed.update(agent=agent, message=message, files=files)
        return "abc123"

    monkeypatch.setattr(security_node, "read_card", fake_read_card)
    monkeypatch.setattr(security_node, "_run_sast_tool", fake_run_sast_tool)
    monkeypatch.setattr(security_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(security_node, "write_card", fake_write_card)
    monkeypatch.setattr(security_node, "commit_as_agent", fake_commit_as_agent)

    updates = await security_node.run(_base_state())

    assert updates["current_phase"] == Phase.AUDIT_AVAL
    assert "status" not in updates
    assert updates["agent_results"][0].status == "success"
    assert updates["total_tokens_sonnet"] == 300
    assert committed["agent"] == "security"
    assert any(p.endswith("security-report.md") for p in written)


async def test_security_high_severity_finding_waits_for_human(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    async def fake_read_card(path):
        return "fiche secu"

    async def fake_run_sast_tool(command, target_dir):
        if "bandit" in command:
            return {"results": [{"issue_severity": "HIGH"}]}
        return {"results": []}

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("# Rapport sécurité\n\nProblème critique trouvé.")

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(security_node, "read_card", fake_read_card)
    monkeypatch.setattr(security_node, "_run_sast_tool", fake_run_sast_tool)
    monkeypatch.setattr(security_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(security_node, "write_card", fake_write_card)
    monkeypatch.setattr(security_node, "commit_as_agent", fake_commit_as_agent)

    updates = await security_node.run(_base_state())

    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert "current_phase" not in updates
    # Le rapport est produit et commité même en cas de blocage.
    assert updates["agent_results"][0].status == "success"


async def test_security_semgrep_error_severity_normalizes_to_high(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    async def fake_read_card(path):
        return "fiche secu"

    async def fake_run_sast_tool(command, target_dir):
        if "semgrep" in command:
            return {"results": [{"extra": {"severity": "ERROR"}}]}
        return {"results": []}

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result()

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(security_node, "read_card", fake_read_card)
    monkeypatch.setattr(security_node, "_run_sast_tool", fake_run_sast_tool)
    monkeypatch.setattr(security_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(security_node, "write_card", fake_write_card)
    monkeypatch.setattr(security_node, "commit_as_agent", fake_commit_as_agent)

    updates = await security_node.run(_base_state())

    assert updates["status"] == RunStatus.WAITING_HUMAN


def test_normalized_severities_bandit():
    payload = {"results": [{"issue_severity": "HIGH"}, {"issue_severity": "LOW"}]}
    assert security_node._normalized_severities("bandit", payload) == ["HIGH", "LOW"]


def test_normalized_severities_semgrep():
    payload = {"results": [{"extra": {"severity": "ERROR"}}, {"extra": {"severity": "INFO"}}]}
    assert security_node._normalized_severities("semgrep", payload) == ["HIGH", "LOW"]


async def test_run_sast_tool_parses_real_subprocess_json(tmp_path: Path):
    result = await security_node._run_sast_tool(
        'python3 -c "import json; print(json.dumps({\'results\': []}))"', tmp_path
    )
    assert result == {"results": []}


async def test_run_sast_tool_invalid_json_raises_runtime_error(tmp_path: Path):
    with pytest.raises(RuntimeError):
        await security_node._run_sast_tool('python3 -c "print(\'pas du json\')"', tmp_path)
