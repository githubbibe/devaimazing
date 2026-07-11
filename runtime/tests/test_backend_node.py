"""
Tests du node Back (studio.nodes.backend).

Toutes les dépendances externes (Ollama, filesystem projet cible, git) sont
mockées : ces tests vérifient le câblage et la logique du node, pas les
tools eux-mêmes (déjà testés séparément).
"""

from pathlib import Path

import pytest
import yaml

import studio.nodes.backend as backend_node
from studio.state import AgentResult, Phase, RunStatus, StudioState


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def project_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    return repo


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, project_repo: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"agents_local": "qwen2.5:7b-instruct"},
        "ollama": {"base_url": "http://localhost:11434", "timeout_seconds": 120},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
        "agents": {"max_iterations": 3},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(project_repo)})
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
    '<<<DEVAIMAZING_FILE path="backend/auth/endpoints.py">>>\n'
    'def login():\n'
    '    ...\n'
    '<<<DEVAIMAZING_END>>>'
)


async def test_backend_stub_phase_writes_files_and_commits(monkeypatch: pytest.MonkeyPatch):
    written = {}
    committed = {}

    async def fake_read_card(path):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        written[str(path)] = content

    async def fake_commit_as_agent(repo_path, agent, message, files):
        committed.update(repo_path=repo_path, agent=agent, message=message, files=files)
        return "abc123"

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
    )

    updates = await backend_node.run(state)

    assert len(updates["agent_results"]) == 1
    result: AgentResult = updates["agent_results"][0]
    assert result.agent == "back"
    assert result.status == "success"
    assert result.output_files == ["backend/auth/endpoints.py"]
    assert any(p.endswith("backend/auth/endpoints.py") for p in written)
    assert committed["agent"] == "back"
    assert committed["files"] == ["backend/auth/endpoints.py"]
    assert updates["total_tokens_ollama"] == 15
    # Pas le dernier agent de la phase (front n'a pas encore joué) -> avance l'index, pas la phase.
    assert updates["current_agent_index"] == 1
    assert "current_phase" not in updates


async def test_backend_includes_existing_file_content_in_prompt(
    monkeypatch: pytest.MonkeyPatch, project_repo: Path
):
    (project_repo / "backend").mkdir(parents=True)
    (project_repo / "backend" / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )

    async def fake_read_card(path):
        return "Modifier `backend/main.py` pour ajouter un handler."

    captured = {}

    async def fake_run_ollama(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
    )

    await backend_node.run(state)

    assert "from fastapi import FastAPI" in captured["user_prompt"]
    assert "Modifier `backend/main.py`" in captured["user_prompt"]


async def test_backend_last_agent_of_stubs_advances_phase(monkeypatch: pytest.MonkeyPatch):
    async def fake_read_card(path):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)

    # "back" est en dernière position de la séquence filtrée (front, back).
    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["front", "back"],
        current_agent_index=1,
        agent_cards={"back": "specs/run-042/back.md"},
    )

    updates = await backend_node.run(state)

    assert updates["current_phase"] == Phase.AUDIT_STUBS
    assert updates["current_agent_index"] == 0


async def test_backend_tu_role_uses_test_commit_prefix_and_extra_skill(monkeypatch: pytest.MonkeyPatch):
    captured_skills = {}

    async def fake_read_card(path):
        return "fiche back-tu"

    async def fake_inject_skills(base_prompt, skill_names, skills_dir):
        captured_skills["names"] = skill_names
        return base_prompt

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    committed = {}

    async def fake_commit_as_agent(repo_path, agent, message, files):
        committed.update(agent=agent, message=message)
        return "abc123"

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "inject_skills", fake_inject_skills)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.IMPLEMENTATION,
        agent_sequence=["back-tu"],
        current_agent_index=0,
        agent_cards={"back-tu": "specs/run-042/back-tu.md"},
    )

    await backend_node.run(state)

    assert "non-regression" in captured_skills["names"]
    assert committed["agent"] == "back"  # back-tu commit sous l'identité back
    assert committed["message"].startswith("test:")


async def test_backend_no_file_blocks_appends_feedback_and_waits_for_human(monkeypatch: pytest.MonkeyPatch):
    feedback_calls = []

    async def fake_read_card(path):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result("Contradiction détectée avec le brief architecte, je m'arrête.")

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "append_feedback", fake_append_feedback)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
    )

    updates = await backend_node.run(state)

    assert len(feedback_calls) == 1
    assert feedback_calls[0][0] == "back"
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert updates["agent_results"][0].status == "feedback_sent"


async def test_backend_accepts_plain_fenced_block_when_single_file_expected(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Repli parse_agent_file_blocks : un modèle producteur qui balise sa sortie
    en ``` markdown standard (au lieu de <<<DEVAIMAZING_FILE>>>) n'est pas
    traité en échec si la fiche ne référence qu'un seul fichier — voir
    docs/roadmap.md (run réel 2026-07-11).
    """
    written = {}

    async def fake_read_card(path):
        return "Modifier `backend/main.py` pour ajouter le handler."

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(
            "Voici le fichier réécrit :\n\n```python\nfrom fastapi import FastAPI\napp = FastAPI()\n```\n"
        )

    async def fake_write_card(path, content):
        written[str(path)] = content

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
    )

    updates = await backend_node.run(state)

    result: AgentResult = updates["agent_results"][0]
    assert result.status == "success"
    assert result.output_files == ["backend/main.py"]
    assert any(p.endswith("backend/main.py") for p in written)
    assert "from fastapi import FastAPI" in list(written.values())[0]


async def test_backend_max_iterations_exceeded_fails_without_calling_ollama(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fail_run_ollama(**kwargs):
        raise AssertionError("run_ollama ne doit pas être appelé au-delà de max_iterations")

    monkeypatch.setattr(backend_node, "run_ollama", fail_run_ollama)

    prior_attempts = [
        AgentResult(agent="back", phase=Phase.STUBS, status="feedback_sent", iteration=i + 1)
        for i in range(3)
    ]
    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_results=prior_attempts,
    )

    updates = await backend_node.run(state)

    assert updates["status"] == RunStatus.FAILED
    assert updates["requires_manual_intervention"] is True
    assert "back" in updates["failed_agents"]
    assert "3" in updates["intervention_reason"]


async def test_backend_records_metrics_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    async def fake_read_card(path):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_BLOCK)

    async def fake_write_card(path, content):
        pass

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
    )

    await backend_node.run(state)

    from studio.metrics import MetricsCollector
    collector = MetricsCollector(tmp_path / "metrics.db")
    summary = await collector.get_run_summary("run-042")
    assert summary["by_agent"]["back"]["task_count"] == 1
