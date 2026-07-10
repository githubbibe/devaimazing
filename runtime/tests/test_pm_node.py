"""
Tests du node PM (studio.nodes.pm) — Claude Code CLI et git mockés.
Le dialogue de cadrage (phase 1) utilise input()/print() réels : ces tests
mockent builtins.input avec des réponses scriptées.
"""

from pathlib import Path

import pytest
import yaml

import studio.nodes.pm as pm_node
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
        "models": {"pm_opus": "claude-opus-4-8", "pm_sonnet": "claude-sonnet-4-6"},
        "checkpoints": {"phase_3_fiches": False},
        "claude_code": {"timeout_seconds": 300, "output_format": "json"},
        "structure": {"specs_dir": "specs/"},
        "git": {"base_branch": "develop"},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    monkeypatch.setenv("DEVAIMAZING_PROJECT", "demo")
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))


def _fake_claude_result(content: str) -> dict:
    return {"content": content, "usage": {"input_tokens": 10, "output_tokens": 20}, "duration_ms": 500}


VALID_FICHE = (
    "**Nom de la feature** : ajout-panier\n"
    "**Objectif brut** : ajouter un panier\n"
)


# --- Phase RECEPTION/CADRAGE ---

async def test_cadrage_question_then_validated(monkeypatch: pytest.MonkeyPatch, repo: Path):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    inputs = iter(["ajout-panier", "oui"])
    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    state = StudioState(
        run_id="run-042", objective_raw="ajouter un panier", current_phase=Phase.CADRAGE,
    )
    updates = await pm_node.run(state)

    assert updates["current_phase"] == Phase.AUDIT_AMONT
    assert updates["card_root_path"] == "specs/run-042/card-root.md"
    assert (repo / "specs" / "run-042" / "card-root.md").read_text(encoding="utf-8") == VALID_FICHE.strip()
    assert updates["total_tokens_opus"] == 60  # 2 tours x (10+20)


async def test_cadrage_records_metrics_with_total_claude_code_calls(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    inputs = iter(["ajout-panier", "oui"])
    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    state = StudioState(run_id="run-042", objective_raw="x", current_phase=Phase.CADRAGE)
    await pm_node.run(state)

    from studio.metrics import MetricsCollector
    collector = MetricsCollector(tmp_path / "metrics.db")
    summary = await collector.get_run_summary("run-042")
    assert summary["by_agent"]["pm"]["task_count"] == 1


async def test_cadrage_rejection_loops_again(monkeypatch: pytest.MonkeyPatch, repo: Path):
    responses = [
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}v2"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    inputs = iter(["non, il manque un critère", "oui"])
    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    state = StudioState(run_id="run-042", objective_raw="x", current_phase=Phase.CADRAGE)
    updates = await pm_node.run(state)

    content = (repo / "specs" / "run-042" / "card-root.md").read_text(encoding="utf-8")
    assert content.endswith("v2")


async def test_reception_phase_also_runs_cadrage_dialogue(monkeypatch: pytest.MonkeyPatch, repo: Path):
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr("builtins.input", lambda prompt="": "oui")

    state = StudioState(run_id="run-042", objective_raw="x", current_phase=Phase.RECEPTION)
    updates = await pm_node.run(state)

    assert updates["current_phase"] == Phase.AUDIT_AMONT


# --- Phase FICHES ---

FICHES_RESPONSE = (
    "SEQUENCE: back, front\n\n"
    '<<<DEVAIMAZING_FILE path="specs/run-042/back.md">>>\n'
    'fiche back\n'
    '<<<DEVAIMAZING_END>>>\n'
    '<<<DEVAIMAZING_FILE path="specs/run-042/front.md">>>\n'
    'fiche front\n'
    '<<<DEVAIMAZING_END>>>'
)


def _write_card_root(repo: Path):
    path = repo / "specs" / "run-042" / "card-root.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(VALID_FICHE, encoding="utf-8")
    (repo / "specs" / "run-042" / "architect-brief.md").write_text("brief", encoding="utf-8")


async def test_fiches_first_pass_no_checkpoint_creates_branch(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(FICHES_RESPONSE)

    async def fake_create_run_branch(repo_path, feature_name, base_branch="develop"):
        assert feature_name == "ajout-panier"
        return "studio/ajout-panier-a3f9c"

    committed = {}

    async def fake_commit_as_agent(repo_path, agent, message, files):
        committed.update(agent=agent, files=files)
        return "abc123"

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(pm_node, "create_run_branch", fake_create_run_branch)
    monkeypatch.setattr(pm_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    updates = await pm_node.run(state)

    assert updates["branch_name"] == "studio/ajout-panier-a3f9c"
    assert updates["current_phase"] == Phase.STUBS
    assert updates["current_agent_index"] == 0
    assert updates["agent_sequence"] == ["back", "front"]
    assert updates["agent_cards"] == {
        "back": "specs/run-042/back.md", "front": "specs/run-042/front.md",
    }
    assert (repo / "specs" / "run-042" / "back.md").read_text(encoding="utf-8") == "fiche back"
    assert committed["agent"] == "pm"


async def test_fiches_first_pass_with_checkpoint_stops_before_branch(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
):
    _write_card_root(repo)

    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"pm_opus": "claude-opus-4-8", "pm_sonnet": "claude-sonnet-4-6"},
        "checkpoints": {"phase_3_fiches": True},
        "claude_code": {"timeout_seconds": 300, "output_format": "json"},
        "structure": {"specs_dir": "specs/"},
        "git": {"base_branch": "develop"},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(FICHES_RESPONSE)

    async def fail_create_run_branch(*args, **kwargs):
        raise AssertionError("create_run_branch ne doit pas être appelé avant validation")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(pm_node, "create_run_branch", fail_create_run_branch)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    updates = await pm_node.run(state)

    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert "current_phase" not in updates
    assert "branch_name" not in updates


async def test_fiches_resume_pass_skips_generation(monkeypatch: pytest.MonkeyPatch, repo: Path):
    _write_card_root(repo)
    (repo / "specs" / "run-042" / "back.md").write_text("fiche back", encoding="utf-8")
    (repo / "specs" / "run-042" / "front.md").write_text("fiche front", encoding="utf-8")

    async def fail_run_claude_code(**kwargs):
        raise AssertionError("run_claude_code ne doit pas être rappelé à la reprise")

    async def fake_create_run_branch(repo_path, feature_name, base_branch="develop"):
        return "studio/ajout-panier-a3f9c"

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(pm_node, "run_claude_code", fail_run_claude_code)
    monkeypatch.setattr(pm_node, "create_run_branch", fake_create_run_branch)
    monkeypatch.setattr(pm_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        agent_sequence=["back", "front"],
        agent_cards={"back": "specs/run-042/back.md", "front": "specs/run-042/front.md"},
    )
    updates = await pm_node.run(state)

    assert updates["branch_name"] == "studio/ajout-panier-a3f9c"
    assert updates["current_phase"] == Phase.STUBS


async def test_fiches_missing_sequence_raises_runtime_error(monkeypatch: pytest.MonkeyPatch, repo: Path):
    _write_card_root(repo)

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("Pas de format reconnu ici.")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    with pytest.raises(RuntimeError):
        await pm_node.run(state)


async def test_fiches_missing_agent_file_block_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            'SEQUENCE: back, front\n\n'
            '<<<DEVAIMAZING_FILE path="specs/run-042/back.md">>>\n'
            'fiche back\n'
            '<<<DEVAIMAZING_END>>>'
        )  # fiche "front" manquante

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    with pytest.raises(RuntimeError):
        await pm_node.run(state)


async def test_fiches_missing_feature_name_raises_runtime_error(repo: Path):
    path = repo / "specs" / "run-042" / "card-root.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("**Objectif brut** : x (sans nom de feature)", encoding="utf-8")

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
    )
    with pytest.raises(RuntimeError):
        await pm_node.run(state)


# --- Phase inconnue ---

async def test_unknown_phase_raises_key_error(repo: Path):
    state = StudioState(run_id="run-042", current_phase=Phase.SECURITE)
    with pytest.raises(KeyError):
        await pm_node.run(state)


# --- Helper pur ---

def test_extract_feature_name():
    assert pm_node._extract_feature_name(VALID_FICHE) == "ajout-panier"


def test_extract_feature_name_missing_raises():
    with pytest.raises(RuntimeError):
        pm_node._extract_feature_name("pas de champ ici")
