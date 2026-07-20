"""
Tests du node Back (studio.nodes.backend).

Toutes les dépendances externes (Ollama, filesystem projet cible, git) sont
mockées : ces tests vérifient le câblage et la logique du node, pas les
tools eux-mêmes (déjà testés séparément).
"""

import json
from pathlib import Path

import pytest
import yaml

import studio.nodes.backend as backend_node
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.pyenv import VerifyFailure


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


@pytest.fixture(autouse=True)
def _verify_python_files_succeeds_by_default(monkeypatch: pytest.MonkeyPatch):
    """
    tools.pyenv.verify_python_files fait un vrai import Python (venv dédié)
    — hors scope de ces tests, qui vérifient le câblage du node, pas la
    vérification elle-même (déjà testée dans test_pyenv.py). Les tests
    dédiés à son échec (voir en bas de ce fichier) l'écrasent explicitement.
    """
    async def fake_verify_python_files(**kwargs):
        return None

    monkeypatch.setattr(backend_node, "verify_python_files", fake_verify_python_files)


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


FILE_OUTPUT = _structured_output({"backend/auth/endpoints.py": "def login():\n    ..."})


def _card_metadata(**overrides) -> dict:
    metadata = {
        "files_to_create": [], "files_to_modify": [], "files_forbidden": [],
        "existing_files_to_read": [], "dependencies": [],
    }
    metadata.update(overrides)
    return metadata


async def test_backend_stub_phase_writes_files_and_commits(monkeypatch: pytest.MonkeyPatch):
    written = {}
    committed = {}

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        written[str(path)] = content

    async def fake_commit_as_agent(repo_path, agent, message, files, tracer=None):
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
        agent_card_metadata={"back": _card_metadata()},
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


async def test_backend_calls_ollama_with_structured_output_schema(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        captured["response_format"] = kwargs.get("response_format")
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    await backend_node.run(state)

    assert captured["response_format"] == backend_node.FILE_OUTPUT_SCHEMA


async def test_backend_includes_existing_file_content_in_prompt(
    monkeypatch: pytest.MonkeyPatch, project_repo: Path
):
    (project_repo / "backend").mkdir(parents=True)
    (project_repo / "backend" / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )

    async def fake_read_card(path, tracer=None):
        return "Modifier `backend/main.py` pour ajouter un handler."

    captured = {}

    async def fake_run_ollama(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_card_metadata={
            "back": _card_metadata(existing_files_to_read=["backend/main.py"]),
        },
    )

    await backend_node.run(state)

    assert "from fastapi import FastAPI" in captured["user_prompt"]
    assert "Modifier `backend/main.py`" in captured["user_prompt"]


async def test_backend_last_agent_of_stubs_advances_phase(monkeypatch: pytest.MonkeyPatch):
    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert updates["current_phase"] == Phase.AUDIT_STUBS
    assert updates["current_agent_index"] == 0


async def test_backend_tu_role_uses_test_commit_prefix_and_extra_skill(monkeypatch: pytest.MonkeyPatch):
    captured_skills = {}

    async def fake_read_card(path, tracer=None):
        return "fiche back-tu"

    async def fake_inject_skills(base_prompt, skill_names, skills_dir):
        captured_skills["names"] = skill_names
        return base_prompt

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    committed = {}

    async def fake_commit_as_agent(repo_path, agent, message, files, tracer=None):
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
        agent_card_metadata={"back-tu": _card_metadata()},
    )

    await backend_node.run(state)

    assert "non-regression" in captured_skills["names"]
    assert committed["agent"] == "back"  # back-tu commit sous l'identité back
    assert committed["message"].startswith("test:")


async def test_backend_blocked_reason_appends_feedback_and_waits_for_human(
    monkeypatch: pytest.MonkeyPatch,
):
    feedback_calls = []

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(
            _structured_output({}, blocked_reason="Contradiction détectée avec le brief architecte.")
        )

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
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert len(feedback_calls) == 1
    assert feedback_calls[0][0] == "back"
    assert feedback_calls[0][1] == "Contradiction détectée avec le brief architecte."
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert updates["agent_results"][0].status == "feedback_sent"


async def test_backend_malformed_json_output_appends_feedback_and_waits_for_human(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Filet de sécurité si le modèle/Ollama ignore la contrainte de schéma
    (voir Notes de tools.ollama.run_ollama) — pas supposé, vérifié.
    """
    feedback_calls = []

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result("pas du JSON du tout")

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
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert len(feedback_calls) == 1
    assert feedback_calls[0][1] == "pas du JSON du tout"
    assert updates["agent_results"][0].status == "feedback_sent"


async def test_backend_absolute_path_output_appends_feedback_and_waits_for_human(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Régression (2026-07-14, run réel) : qwen2.5:1.5b-instruct a produit un
    chemin de fichier absolu ("/backend/main.py", imitation littérale de
    prompts/backend.md) — sans garde-fou, config.repo_path / "/backend/
    main.py" ignore silencieusement repo_path (pathlib) et le node tente une
    écriture hors du repo cible. tools.filesystem.parse_structured_file_output
    rejette désormais ce chemin (ValueError), déjà absorbée par le
    `except ValueError` existant du node — même dégradation que pour un JSON
    malformé, pas de crash.
    """
    feedback_calls = []

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(
            '{"files": [{"path": "/backend/main.py", "content": "x"}], "blocked_reason": ""}'
        )

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
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert len(feedback_calls) == 1
    assert updates["agent_results"][0].status == "feedback_sent"
    assert updates["status"] == RunStatus.WAITING_HUMAN


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
    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_card_metadata={"back": _card_metadata()},
    )

    await backend_node.run(state)

    from studio.metrics import MetricsCollector
    collector = MetricsCollector(tmp_path / "metrics.db")
    summary = await collector.get_run_summary("run-042")
    assert summary["by_agent"]["back"]["task_count"] == 1


async def test_backend_writes_trace_events_for_run(
    monkeypatch: pytest.MonkeyPatch, project_repo: Path
):
    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    await backend_node.run(state)

    import json
    trace_path = project_repo / "specs" / "run-042" / "trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "node_enter"
    assert events[0]["agent"] == "back"
    assert events[0]["phase"] == "STUBS"
    assert events[-1]["event"] == "node_exit"
    assert events[-1]["status"] == "success"


async def test_backend_verify_failure_appends_feedback_and_waits_for_human(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Régression run-20260714-205712 (2026-07-19/20, todo-list) : back
    committait des fichiers syntaxiquement/logiquement invalides (imports
    manquants, NameError) qui n'étaient détectés que par l'audit Architecte,
    coûteux et tardif — voir tools.pyenv.verify_python_files et
    docs/roadmap.md. Un échec de vérification doit suivre le même chemin
    que blocked_reason : feedback ajouté, pas de commit, run en attente.
    """
    feedback_calls = []
    committed = {"called": False}

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    async def fake_commit_as_agent(**kwargs):
        committed["called"] = True
        return "abc123"

    async def fake_verify_python_files(**kwargs):
        return VerifyFailure(
            file="backend/auth/endpoints.py",
            message="Échec d'import de backend.auth.endpoints : NameError: name 'X' is not defined",
        )

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "append_feedback", fake_append_feedback)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(backend_node, "verify_python_files", fake_verify_python_files)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert committed["called"] is False
    assert len(feedback_calls) == 1
    assert feedback_calls[0][0] == "back"
    assert "NameError" in feedback_calls[0][1]
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert updates["agent_results"][0].status == "feedback_sent"


async def test_backend_verify_success_proceeds_to_commit(monkeypatch: pytest.MonkeyPatch):
    """Non-régression : une vérification qui réussit ne bloque rien (chemin nominal inchangé)."""
    committed = {}
    verify_calls = []

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(repo_path, agent, message, files, tracer=None):
        committed.update(agent=agent, files=files)
        return "abc123"

    async def fake_verify_python_files(**kwargs):
        verify_calls.append(kwargs)
        return None

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(backend_node, "verify_python_files", fake_verify_python_files)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert len(verify_calls) == 1
    assert verify_calls[0]["files"] == {"backend/auth/endpoints.py": "def login():\n    ..."}
    assert committed["agent"] == "back"
    assert updates["agent_results"][0].status == "success"


async def test_backend_inner_retry_converges_without_feedback_sent(monkeypatch: pytest.MonkeyPatch):
    """
    La boucle de retry interne (inner_retry_limit) doit corriger un échec de
    vérification DANS la même activation : 1er appel Ollama produit un
    fichier qui échoue à la vérification, 2e appel (prompt "mode correction
    ciblée (tour interne)") produit un fichier qui passe — le node doit
    committer normalement, sans jamais passer par feedback_sent/WAITING_HUMAN
    (voir docs/roadmap.md, 2026-07-20).
    """
    calls = {"count": 0}
    verify_call_count = {"count": 0}
    committed = {}
    feedback_calls = []

    BROKEN_OUTPUT = _structured_output({"backend/auth/endpoints.py": "def login(:\n    ..."})
    FIXED_OUTPUT = _structured_output({"backend/auth/endpoints.py": "def login():\n    ..."})

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        calls["count"] += 1
        content = BROKEN_OUTPUT if calls["count"] == 1 else FIXED_OUTPUT
        return _fake_ollama_result(content, tokens_prompt=5, tokens_completion=10, duration_ms=100)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_commit_as_agent(repo_path, agent, message, files, tracer=None):
        committed.update(agent=agent, files=files)
        return "abc123"

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    async def fake_verify_python_files(**kwargs):
        verify_call_count["count"] += 1
        if verify_call_count["count"] == 1:
            return VerifyFailure(
                file="backend/auth/endpoints.py",
                message="Erreur de syntaxe dans backend/auth/endpoints.py : invalid syntax",
            )
        return None

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(backend_node, "append_feedback", fake_append_feedback)
    monkeypatch.setattr(backend_node, "verify_python_files", fake_verify_python_files)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert calls["count"] == 2
    assert verify_call_count["count"] == 2
    assert feedback_calls == []
    assert committed["agent"] == "back"
    assert "status" not in updates
    assert updates["agent_results"][0].status == "success"
    assert updates["agent_results"][0].tokens_prompt == 10
    assert updates["agent_results"][0].tokens_completion == 20


async def test_backend_inner_retry_exhausted_falls_back_to_feedback_sent(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Si tous les tours internes échouent (inner_retry_limit atteint sans
    vérification réussie), le node doit retomber sur le chemin existant
    feedback_sent/WAITING_HUMAN — un seul appel à append_feedback, pas un
    par tentative interne.
    """
    calls = {"count": 0}
    feedback_calls = []

    BROKEN_OUTPUT = _structured_output({"backend/auth/endpoints.py": "def login(:\n    ..."})

    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        calls["count"] += 1
        return _fake_ollama_result(BROKEN_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    async def fake_commit_as_agent(**kwargs):
        raise AssertionError("commit_as_agent ne doit pas être appelé si la vérification échoue toujours")

    async def fake_verify_python_files(**kwargs):
        return VerifyFailure(
            file="backend/auth/endpoints.py",
            message="Erreur de syntaxe dans backend/auth/endpoints.py : invalid syntax",
        )

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "append_feedback", fake_append_feedback)
    monkeypatch.setattr(backend_node, "commit_as_agent", fake_commit_as_agent)
    monkeypatch.setattr(backend_node, "verify_python_files", fake_verify_python_files)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert calls["count"] == 3  # inner_retry_limit par défaut (studio.yml)
    assert len(feedback_calls) == 1
    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["agent_results"][0].status == "feedback_sent"


async def test_backend_verify_failure_sets_retry_scope(monkeypatch: pytest.MonkeyPatch):
    """
    Régression run-20260714-205712 (2026-07-20) : le fichier fautif connu
    avec certitude (VerifyFailure) doit être ciblé pour le prochain tour,
    pas noyé dans une régénération complète.
    """
    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_append_feedback(card_path, agent_source, feedback):
        pass

    async def fake_verify_python_files(**kwargs):
        return VerifyFailure(file="backend/auth/endpoints.py", message="NameError: X non défini")

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "append_feedback", fake_append_feedback)
    monkeypatch.setattr(backend_node, "verify_python_files", fake_verify_python_files)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert updates["retry_scope"]["back"] == {
        "backend/auth/endpoints.py": "NameError: X non défini"
    }


async def test_backend_verify_failure_related_files_all_targeted(monkeypatch: pytest.MonkeyPatch):
    """
    Régression run-20260714-205712 (2026-07-20) : import circulaire — le
    fichier importé (crud.py) ET le fichier réellement en cause
    (models.py, via VerifyFailure.related_files) doivent tous deux
    apparaître dans retry_scope, sinon le tour suivant ne montre jamais le
    vrai fichier fautif au modèle (6 tours identiques observés en run réel).
    """
    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
        pass

    async def fake_append_feedback(card_path, agent_source, feedback):
        pass

    async def fake_verify_python_files(**kwargs):
        return VerifyFailure(
            file="backend/crud.py",
            message="ImportError: cannot import name 'Base' (import circulaire)",
            related_files=["backend/models.py"],
        )

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "write_card", fake_write_card)
    monkeypatch.setattr(backend_node, "append_feedback", fake_append_feedback)
    monkeypatch.setattr(backend_node, "verify_python_files", fake_verify_python_files)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    updates = await backend_node.run(state)

    assert set(updates["retry_scope"]["back"]) == {"backend/crud.py", "backend/models.py"}


async def test_backend_success_clears_retry_scope(monkeypatch: pytest.MonkeyPatch, project_repo: Path):
    """Un tour réussi vide le retry_scope de l'agent — retour au régime normal."""
    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
        retry_scope={"back": {"backend/auth/endpoints.py": "erreur précédente"}},
    )

    updates = await backend_node.run(state)

    assert "back" not in updates["retry_scope"]


async def test_backend_blocked_reason_clears_retry_scope(monkeypatch: pytest.MonkeyPatch):
    """
    Un blocage de l'agent lui-même (blocked_reason, fichier non
    identifiable) fait retomber en régénération complète — retry_scope vidé.
    """
    async def fake_read_card(path, tracer=None):
        return "fiche back"

    async def fake_run_ollama(**kwargs):
        return _fake_ollama_result(
            _structured_output({}, blocked_reason="Contradiction détectée.")
        )

    async def fake_append_feedback(card_path, agent_source, feedback):
        pass

    monkeypatch.setattr(backend_node, "read_card", fake_read_card)
    monkeypatch.setattr(backend_node, "run_ollama", fake_run_ollama)
    monkeypatch.setattr(backend_node, "append_feedback", fake_append_feedback)

    state = StudioState(
        run_id="run-042",
        current_phase=Phase.STUBS,
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
        retry_scope={"back": {"backend/auth/endpoints.py": "erreur précédente"}},
    )

    updates = await backend_node.run(state)

    assert "back" not in updates["retry_scope"]


async def test_backend_targeted_mode_prompt_contains_only_flagged_file(
    monkeypatch: pytest.MonkeyPatch, project_repo: Path
):
    """
    Régression run-20260714-205712 (2026-07-20) : en mode ciblé, le prompt
    ne doit contenir QUE le contenu actuel du fichier fautif + l'erreur
    précise — pas l'historique de feedback cumulé (source de non-
    convergence déjà diagnostiquée), pas les autres fichiers du périmètre.
    """
    card = (
        "## Tâche\n\nImplémente le endpoint login.\n\n"
        "## Feedback\n\n"
        "[2026-07-19] [architect] : vieux feedback obsolète qui ne doit plus être vu.\n"
    )

    async def fake_read_card(path, tracer=None):
        return card

    (project_repo / "backend" / "auth").mkdir(parents=True)
    (project_repo / "backend" / "auth" / "endpoints.py").write_text(
        "def login():\n    raise NotImplementedError\n", encoding="utf-8"
    )

    captured = {}

    async def fake_run_ollama(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
        retry_scope={"back": {"backend/auth/endpoints.py": "NameError: X non défini"}},
    )

    await backend_node.run(state)

    prompt = captured["user_prompt"]
    assert "def login():\n    raise NotImplementedError" in prompt
    assert "NameError: X non défini" in prompt
    assert "Implémente le endpoint login." in prompt
    assert "vieux feedback obsolète" not in prompt


async def test_backend_targeted_mode_prompt_contains_all_related_files(
    monkeypatch: pytest.MonkeyPatch, project_repo: Path
):
    """
    Régression run-20260714-205712 (2026-07-20) : import circulaire — le
    prompt ciblé doit contenir le contenu des DEUX fichiers (crud.py ET
    models.py), sinon le modèle ne peut pas corriger un bug situé dans le
    fichier lié plutôt que dans le fichier importé en tête.
    """
    async def fake_read_card(path, tracer=None):
        return "## Tâche\n\nCorrige le backend.\n"

    (project_repo / "backend").mkdir(parents=True)
    (project_repo / "backend" / "crud.py").write_text(
        "from backend.models import Base\n", encoding="utf-8"
    )
    (project_repo / "backend" / "models.py").write_text(
        "from backend.database import Base\n", encoding="utf-8"
    )

    captured = {}

    async def fake_run_ollama(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
        retry_scope={
            "back": {
                "backend/crud.py": "ImportError circulaire",
                "backend/models.py": "ImportError circulaire",
            }
        },
    )

    await backend_node.run(state)

    prompt = captured["user_prompt"]
    assert "from backend.models import Base" in prompt
    assert "from backend.database import Base" in prompt


async def test_backend_no_retry_scope_uses_full_regeneration(monkeypatch: pytest.MonkeyPatch):
    """Non-régression : sans retry_scope, le prompt reste la fiche complète telle quelle."""
    async def fake_read_card(path, tracer=None):
        return "fiche back complète"

    captured = {}

    async def fake_run_ollama(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_ollama_result(FILE_OUTPUT)

    async def fake_write_card(path, content, tracer=None):
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
        agent_sequence=["back"],
        current_agent_index=0,
        agent_cards={"back": "specs/run-042/back.md"},
        agent_card_metadata={"back": _card_metadata()},
    )

    await backend_node.run(state)

    assert "fiche back complète" in captured["user_prompt"]
    assert "MODE CORRECTION CIBLÉE" not in captured["user_prompt"]
