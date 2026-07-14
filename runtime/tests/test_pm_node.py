"""
Tests du node PM (studio.nodes.pm) — Claude Code CLI et git mockés.
Le dialogue de cadrage (phase 1) utilise input()/print() réels : ces tests
mockent builtins.input avec des réponses scriptées.
"""

from pathlib import Path

import pytest
import yaml

import studio.nodes.pm as pm_node
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


def _fake_claude_result(content: str, structured_output: dict | None = None) -> dict:
    return {
        "content": content,
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "duration_ms": 500,
        "structured_output": structured_output,
    }


def _card_metadata(**overrides) -> dict:
    metadata = {
        "files_to_create": [], "files_to_modify": [], "files_forbidden": [],
        "existing_files_to_read": [], "dependencies": [],
    }
    metadata.update(overrides)
    return metadata


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
    '<<<DEVAIMAZING_FILE path="specs/run-042/back.md">>>\n'
    'fiche back\n\n## Feedback\n\n_Aucun feedback pour l\'instant._\n'
    '<<<DEVAIMAZING_END>>>\n'
    '<<<DEVAIMAZING_FILE path="specs/run-042/front.md">>>\n'
    'fiche front\n\n## Feedback\n\n_Aucun feedback pour l\'instant._\n'
    '<<<DEVAIMAZING_END>>>'
)

FICHES_STRUCTURED_OUTPUT = {
    "sequence": ["back", "front"],
    "cards": [
        {"agent": "back", **_card_metadata()},
        {"agent": "front", **_card_metadata()},
    ],
}


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
        return _fake_claude_result(FICHES_RESPONSE, structured_output=FICHES_STRUCTURED_OUTPUT)

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
    assert updates["agent_card_metadata"] == {
        "back": _card_metadata(), "front": _card_metadata(),
    }
    assert "fiche back" in (repo / "specs" / "run-042" / "back.md").read_text(encoding="utf-8")
    assert committed["agent"] == "pm"


async def test_fiches_two_separate_calls_metadata_then_prose(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    """Étape 1 (schéma seul) puis étape 2 (prose seule, informée par l'étape 1) —
    voir docs/roadmap.md 2026-07-15 : un appel unique mêlant les deux échouait
    systématiquement en pratique."""
    _write_card_root(repo)

    calls = []

    async def fake_run_claude_code(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _fake_claude_result("", structured_output=FICHES_STRUCTURED_OUTPUT)
        return _fake_claude_result(FICHES_RESPONSE)

    async def fake_create_run_branch(repo_path, feature_name, base_branch="develop"):
        return "studio/ajout-panier-a3f9c"

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(pm_node, "create_run_branch", fake_create_run_branch)
    monkeypatch.setattr(pm_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    await pm_node.run(state)

    assert len(calls) == 2
    assert calls[0]["response_schema"] is not None
    assert calls[1].get("response_schema") is None
    # L'appel 2 reçoit bien les métadonnées déterminées par l'appel 1.
    assert "back" in calls[1]["prompt"]
    assert "front" in calls[1]["prompt"]


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
        return _fake_claude_result(FICHES_RESPONSE, structured_output=FICHES_STRUCTURED_OUTPUT)

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


async def test_fiches_missing_structured_output_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    calls = []

    async def fake_run_claude_code(**kwargs):
        calls.append(kwargs)
        return _fake_claude_result("Pas de format reconnu ici.")  # structured_output=None

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    with pytest.raises(RuntimeError):
        await pm_node.run(state)

    # L'échec de l'étape 1 (métadonnées) est fatal avant tout appel de l'étape 2.
    assert len(calls) == 1


async def test_fiches_agent_missing_from_structured_output_cards_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            FICHES_RESPONSE,
            structured_output={
                "sequence": ["back", "front"],
                "cards": [{"agent": "back", **_card_metadata()}],  # "front" manquant
            },
        )

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    with pytest.raises(RuntimeError):
        await pm_node.run(state)

    assert not (repo / "specs" / "run-042" / "back.md").is_file()
    assert not (repo / "specs" / "run-042" / "front.md").is_file()


async def test_fiches_missing_agent_file_block_sends_feedback_and_waits(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            '<<<DEVAIMAZING_FILE path="specs/run-042/back.md">>>\n'
            'fiche back\n'
            '<<<DEVAIMAZING_END>>>',  # fiche "front" manquante
            structured_output=FICHES_STRUCTURED_OUTPUT,
        )

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    updates = await pm_node.run(state)

    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert "agent_cards" not in updates
    assert "current_phase" not in updates
    last_result = updates["agent_results"][-1]
    assert last_result.agent == "pm"
    assert last_result.status == "feedback_sent"
    assert last_result.iteration == 1
    assert last_result.feedback
    assert not (repo / "specs" / "run-042" / "back.md").is_file()
    assert not (repo / "specs" / "run-042" / "front.md").is_file()


async def test_fiches_missing_feedback_section_sends_feedback_and_waits(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            '<<<DEVAIMAZING_FILE path="specs/run-042/back.md">>>\n'
            'fiche back sans section feedback\n'
            '<<<DEVAIMAZING_END>>>\n'
            '<<<DEVAIMAZING_FILE path="specs/run-042/front.md">>>\n'
            'fiche front\n\n## Feedback\n\n_Aucun feedback pour l\'instant._\n'
            '<<<DEVAIMAZING_END>>>',  # fiche "back" sans section '## Feedback'
            structured_output=FICHES_STRUCTURED_OUTPUT,
        )

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    updates = await pm_node.run(state)

    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["agent_results"][-1].status == "feedback_sent"
    assert not (repo / "specs" / "run-042" / "back.md").is_file()
    assert not (repo / "specs" / "run-042" / "front.md").is_file()


async def test_fiches_no_file_blocks_at_all_sends_feedback_and_waits(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    """Reproduit l'incident réel du 2026-07-14 (run-20260714-205712, projet
    todo-list) : le canal structuré (JSON schema) réussit, mais le canal
    prose ne contient aucun bloc <<<DEVAIMAZING_FILE>>> reconnu (constaté
    en pratique après un refus d'outil Bash côté Claude Code CLI)."""
    _write_card_root(repo)

    raw_content = (
        "Je n'ai pas pu utiliser l'outil demandé, voici une explication à la "
        "place des fiches attendues."
    )

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(raw_content, structured_output=FICHES_STRUCTURED_OUTPUT)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    updates = await pm_node.run(state)

    assert updates["status"] == RunStatus.WAITING_HUMAN
    assert updates["awaiting_human_validation"] is True
    assert "agent_cards" not in updates
    last_result = updates["agent_results"][-1]
    assert last_result.status == "feedback_sent"
    assert raw_content in last_result.feedback


async def test_fiches_second_attempt_after_feedback_increments_iteration(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("toujours pas de bloc reconnu", structured_output=FICHES_STRUCTURED_OUTPUT)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
        agent_results=[
            AgentResult(agent="pm", phase=Phase.FICHES, status="feedback_sent", iteration=1),
        ],
    )
    updates = await pm_node.run(state)

    assert updates["agent_results"][-1].iteration == 2


async def test_fiches_max_iterations_exceeded_skips_llm_call(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    async def fail_run_claude_code(**kwargs):
        raise AssertionError("run_claude_code ne doit pas être appelé au-delà de max_iterations")

    monkeypatch.setattr(pm_node, "run_claude_code", fail_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
        agent_results=[
            AgentResult(agent="pm", phase=Phase.FICHES, status="feedback_sent", iteration=i)
            for i in (1, 2, 3)
        ],
    )
    updates = await pm_node.run(state)

    assert updates["status"] == RunStatus.FAILED
    assert updates["requires_manual_intervention"] is True
    assert "pm" in updates["failed_agents"]


async def test_fiches_existing_file_to_read_missing_on_disk_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            FICHES_RESPONSE,
            structured_output={
                "sequence": ["back", "front"],
                "cards": [
                    {
                        "agent": "back",
                        **_card_metadata(existing_files_to_read=["backend/absent.py"]),
                    },
                    {"agent": "front", **_card_metadata()},
                ],
            },
        )

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    with pytest.raises(RuntimeError):
        await pm_node.run(state)

    assert not (repo / "specs" / "run-042" / "back.md").is_file()
    assert not (repo / "specs" / "run-042" / "front.md").is_file()


async def test_fiches_existing_file_to_read_present_on_disk_succeeds(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    _write_card_root(repo)
    (repo / "backend").mkdir()
    (repo / "backend" / "main.py").write_text("app = 1", encoding="utf-8")

    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result(
            FICHES_RESPONSE,
            structured_output={
                "sequence": ["back", "front"],
                "cards": [
                    {
                        "agent": "back",
                        **_card_metadata(existing_files_to_read=["backend/main.py"]),
                    },
                    {"agent": "front", **_card_metadata()},
                ],
            },
        )

    async def fake_create_run_branch(repo_path, feature_name, base_branch="develop"):
        return "studio/ajout-panier-a3f9c"

    async def fake_commit_as_agent(**kwargs):
        return "abc123"

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    monkeypatch.setattr(pm_node, "create_run_branch", fake_create_run_branch)
    monkeypatch.setattr(pm_node, "commit_as_agent", fake_commit_as_agent)

    state = StudioState(
        run_id="run-042", current_phase=Phase.FICHES, card_root_path="specs/run-042/card-root.md",
        architect_brief_path="specs/run-042/architect-brief.md",
    )
    updates = await pm_node.run(state)

    assert updates["agent_card_metadata"]["back"]["existing_files_to_read"] == ["backend/main.py"]
    assert (repo / "specs" / "run-042" / "back.md").is_file()


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
