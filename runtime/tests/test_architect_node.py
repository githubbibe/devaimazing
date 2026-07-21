"""
Tests du node Architecte (studio.nodes.architect) — dépendances externes
(Claude Code CLI, git) mockées.
"""

from pathlib import Path

import pytest
import yaml

import studio.nodes.architect as architect_node
from studio.state import Phase, RunStatus, StudioState


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "specs" / "run-042").mkdir(parents=True)
    (repo / "specs" / "run-042" / "card-root.md").write_text("objectif du run", encoding="utf-8")
    (repo / "specs" / "run-042" / "back.md").write_text("fiche back", encoding="utf-8")
    (repo / "specs" / "run-042" / "front.md").write_text("fiche front", encoding="utf-8")
    (repo / "specs" / "run-042" / "architect-brief.md").write_text("brief existant", encoding="utf-8")
    return repo


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"agent_auditor": "claude-sonnet-4-6"},
        "checkpoints": {
            "phase_2_audit_amont": False,
            "phase_5_audit_stubs": False,
            "phase_9_audit_aval": False,
        },
        "claude_code": {"timeout_seconds": 300, "output_format": "json"},
        "structure": {"specs_dir": "specs/"},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
        "agents": {"max_iterations": 3},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    monkeypatch.setenv("DEVAIMAZING_PROJECT", "demo")
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))


def _fake_claude_result(content: str) -> dict:
    return {"content": content, "usage": {"input_tokens": 100, "output_tokens": 200}, "duration_ms": 1000}


def _base_state(**overrides) -> StudioState:
    defaults = dict(
        run_id="run-042",
        current_phase=Phase.AUDIT_AMONT,
        card_root_path="specs/run-042/card-root.md",
        agent_sequence=["back", "front"],
        agent_cards={"back": "specs/run-042/back.md", "front": "specs/run-042/front.md"},
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    defaults.update(overrides)
    return StudioState(**defaults)


# --- Phase AUDIT_AMONT ---

async def test_audit_amont_writes_brief_and_advances_to_fiches(monkeypatch: pytest.MonkeyPatch, repo: Path):
    committed = {}

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("# Brief architectural\n\nContenu du brief.")

    async def fake_commit_as_agent(repo_path, agent, message, files, tracer=None):
        committed.update(agent=agent, message=message, files=files)
        return "abc123"

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(architect_node, "commit_as_agent", fake_commit_as_agent)

    state = _base_state(current_phase=Phase.AUDIT_AMONT)
    updates = await architect_node.run(state)

    assert updates["current_phase"] == Phase.FICHES
    assert updates["architect_brief_path"] == "specs/run-042/architect-brief.md"
    assert (repo / "specs" / "run-042" / "architect-brief.md").read_text(encoding="utf-8") == (
        "# Brief architectural\n\nContenu du brief."
    )
    assert committed["agent"] == "architect"
    assert "status" not in updates  # checkpoint désactivé dans la fixture


async def test_audit_amont_with_checkpoint_enabled_waits_for_human(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"agent_auditor": "claude-sonnet-4-6"},
        "checkpoints": {"phase_2_audit_amont": True},
        "claude_code": {"timeout_seconds": 300, "output_format": "json"},
        "structure": {"specs_dir": "specs/"},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("# Brief")

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(architect_node, "commit_as_agent", fake_commit_as_agent)

    updates = await architect_node.run(_base_state(current_phase=Phase.AUDIT_AMONT))

    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert updates["current_phase"] == Phase.FICHES  # la transition reste déterminée


# --- Phase AUDIT_STUBS ---

async def test_audit_stubs_conforme_advances_to_implementation(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("STATUT: CONFORME")

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)

    state = _base_state(current_phase=Phase.AUDIT_STUBS)
    updates = await architect_node.run(state)

    assert updates["current_phase"] == Phase.IMPLEMENTATION
    assert updates["current_agent_index"] == 0


async def test_audit_stubs_ecart_sends_back_to_faulty_agent(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    feedback_calls = []

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            "STATUT: ECART\nAGENT: front\nFEEDBACK: signature incohérente avec le brief"
        )

    async def fake_append_feedback(card_path, agent_source, feedback):
        feedback_calls.append((agent_source, feedback))

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(architect_node, "append_feedback", fake_append_feedback)

    state = _base_state(current_phase=Phase.AUDIT_STUBS, agent_sequence=["back", "front"])
    updates = await architect_node.run(state)

    assert updates["current_phase"] == Phase.STUBS
    assert updates["current_agent_index"] == 1  # position de "front" dans la séquence stubs
    assert updates["failed_agents"] == ["front"]
    assert feedback_calls[0] == ("architect", "signature incohérente avec le brief")
    assert "retry_scope" not in updates  # aucun fichier extractible du feedback


async def test_audit_stubs_ecart_with_flagged_file_sets_retry_scope(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    """
    Régression run-20260714-205712 (2026-07-20) : un écart citant un
    fichier précis doit cibler ce fichier au tour suivant (mode ciblé déjà
    existant, chantiers 2/3), pas déclencher une régénération complète du
    périmètre de l'agent fautif.
    """
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            "STATUT: ECART\nAGENT: back\nFEEDBACK: "
            "`backend/schemas.py` — TaskResponse hérite de TaskCreate sans le champ terminé."
        )

    async def fake_append_feedback(card_path, agent_source, feedback):
        pass

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(architect_node, "append_feedback", fake_append_feedback)

    state = _base_state(
        current_phase=Phase.AUDIT_STUBS,
        agent_sequence=["back", "front"],
        agent_card_metadata={
            "back": {
                "files_to_create": ["backend/schemas.py", "backend/exceptions.py"],
                "files_to_modify": [],
            }
        },
    )
    updates = await architect_node.run(state)

    assert updates["retry_scope"]["back"] == {
        "backend/schemas.py": (
            "`backend/schemas.py` — TaskResponse hérite de TaskCreate sans le champ terminé."
        )
    }


async def test_audit_stubs_ecart_preserves_other_agents_retry_scope(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    """Un nouveau retry_scope pour l'agent fautif n'écrase pas celui d'un autre agent."""
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            "STATUT: ECART\nAGENT: back\nFEEDBACK: `backend/schemas.py` incomplet."
        )

    async def fake_append_feedback(card_path, agent_source, feedback):
        pass

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(architect_node, "append_feedback", fake_append_feedback)

    state = _base_state(
        current_phase=Phase.AUDIT_STUBS,
        agent_sequence=["back", "front"],
        agent_card_metadata={
            "back": {"files_to_create": ["backend/schemas.py"], "files_to_modify": []},
        },
        retry_scope={"front": {"frontend/App.tsx": "erreur front non liée"}},
    )
    updates = await architect_node.run(state)

    assert updates["retry_scope"]["front"] == {"frontend/App.tsx": "erreur front non liée"}
    assert "backend/schemas.py" in updates["retry_scope"]["back"]


async def test_audit_stubs_missing_statut_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("Tout va bien, pas de format particulier.")

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)

    with pytest.raises(RuntimeError):
        await architect_node.run(_base_state(current_phase=Phase.AUDIT_STUBS))


# --- Phase AUDIT_AVAL ---

async def test_audit_aval_writes_multiple_files_and_advances_to_cloture(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    content = (
        '<<<DEVAIMAZING_FILE path="docs/adr/0011-exemple.md">>>\n'
        'contenu adr\n'
        '<<<DEVAIMAZING_END>>>\n'
        '<<<DEVAIMAZING_FILE path="docs/architect-map.md">>>\n'
        'contenu map\n'
        '<<<DEVAIMAZING_END>>>'
    )

    committed = {}

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(content)

    async def fake_commit_as_agent(repo_path, agent, message, files, tracer=None):
        committed.update(agent=agent, files=files)
        return "abc123"

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(architect_node, "commit_as_agent", fake_commit_as_agent)

    state = _base_state(current_phase=Phase.AUDIT_AVAL)
    updates = await architect_node.run(state)

    assert updates["current_phase"] == Phase.CLOTURE
    assert (repo / "docs" / "adr" / "0011-exemple.md").read_text(encoding="utf-8") == "contenu adr"
    assert (repo / "docs" / "architect-map.md").read_text(encoding="utf-8") == "contenu map"
    assert set(committed["files"]) == {"docs/adr/0011-exemple.md", "docs/architect-map.md"}


# --- Phase inconnue ---

async def test_unknown_phase_raises_key_error(repo: Path):
    state = _base_state(current_phase=Phase.SECURITE)
    with pytest.raises(KeyError):
        await architect_node.run(state)


async def test_audit_amont_records_metrics(monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path):
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("# Brief")

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(architect_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(architect_node, "commit_as_agent", fake_commit_as_agent)

    await architect_node.run(_base_state(current_phase=Phase.AUDIT_AMONT))

    from studio.metrics import MetricsCollector
    collector = MetricsCollector(tmp_path / "metrics.db")
    summary = await collector.get_run_summary("run-042")
    assert summary["by_agent"]["architect"]["task_count"] == 1


# --- Helpers purs ---

def test_parse_audit_decision_conforme():
    conforme, agent, feedback = architect_node._parse_audit_decision("STATUT: CONFORME")
    assert conforme is True
    assert agent is None


def test_parse_audit_decision_ecart():
    conforme, agent, feedback = architect_node._parse_audit_decision(
        "STATUT: ECART\nAGENT: back\nFEEDBACK: manque la gestion d'erreur"
    )
    assert conforme is False
    assert agent == "back"
    assert feedback == "manque la gestion d'erreur"


def test_extract_feedback_files_exact_path_match():
    feedback = "`backend/schemas.py` — TaskResponse hérite de TaskCreate sans le champ terminé."
    known_files = ["backend/schemas.py", "backend/exceptions.py"]
    assert architect_node._extract_feedback_files(feedback, known_files) == ["backend/schemas.py"]


def test_extract_feedback_files_resolves_bare_filename():
    feedback = "`crud.py` ligne 4 importe un symbole absent de schemas.py."
    known_files = ["backend/crud.py", "backend/schemas.py"]
    assert architect_node._extract_feedback_files(feedback, known_files) == ["backend/crud.py"]


def test_extract_feedback_files_ignores_code_symbols():
    feedback = "`TaskResponse` et `HTTPException` posent problème dans `backend/schemas.py`."
    known_files = ["backend/schemas.py"]
    assert architect_node._extract_feedback_files(feedback, known_files) == ["backend/schemas.py"]


def test_extract_feedback_files_drops_ambiguous_bare_filename():
    """
    Deux fichiers connus se terminent par /crud.py (cas construit — en
    pratique le PM ne déclare pas deux fichiers homonymes) : le candidat
    `crud.py` n'est pas résolvable avec certitude, il est ignoré plutôt
    que deviné.
    """
    feedback = "`crud.py` a un problème."
    known_files = ["backend/crud.py", "backend/legacy/crud.py"]
    assert architect_node._extract_feedback_files(feedback, known_files) == []


def test_extract_feedback_files_no_match_returns_empty():
    feedback = "Le module ne respecte pas le brief architectural."
    known_files = ["backend/schemas.py"]
    assert architect_node._extract_feedback_files(feedback, known_files) == []


def test_extract_feedback_files_deduplicates_across_exact_and_bare():
    feedback = "`backend/crud.py` importe mal. Voir aussi `crud.py` ligne 4."
    known_files = ["backend/crud.py"]
    assert architect_node._extract_feedback_files(feedback, known_files) == ["backend/crud.py"]
