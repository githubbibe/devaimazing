"""
Tests du CLI devaimazing (studio.cli).

Tests synchrones (pas `async def`) : chaque commande CLI appelle
asyncio.run() en interne, ce qui échoue si on l'invoque depuis une
coroutine de test déjà dans une boucle asyncio active (pytest-asyncio
mode auto). CliRunner.invoke() reste un appel synchrone normal ici.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

import studio.cli as cli_module
from studio.cli import main
from studio.state import Phase, RunStatus


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
        "models": {"pm_opus": "claude-opus-4-8"},
        "metrics": {"db_path": str(tmp_path / "metrics.db")},
        "state": {"db_path": str(tmp_path / "state.db")},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("DEVAIMAZING_PROJECT", raising=False)
    return config_dir


class _FakeSnapshot:
    def __init__(self, values: dict):
        self.values = values


def _fake_checkpointer(closed: list) -> SimpleNamespace:
    """
    Checkpointer factice : `closed` accumule un True à chaque appel de
    `.conn.close()`, pour vérifier dans les tests que cli.py ferme bien la
    connexion SQLite après usage (voir régression :
    test_run_closes_checkpointer_connection_even_on_error).
    """
    async def _close():
        closed.append(True)

    return SimpleNamespace(conn=SimpleNamespace(close=_close))


# --- run ---

def test_run_dry_run_does_not_invoke_graph(monkeypatch: pytest.MonkeyPatch):
    async def fail_build_graph(config):
        raise AssertionError("build_graph ne doit pas être appelé en --dry-run")

    monkeypatch.setattr(cli_module, "build_graph", fail_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "ajouter un panier", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry-run" in result.output


def test_run_completed_prints_success(monkeypatch: pytest.MonkeyPatch):
    closed = []

    async def fake_ainvoke(state, config):
        return {"status": RunStatus.COMPLETED, "current_phase": Phase.CLOTURE}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer(closed))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code == 0
    assert "terminé" in result.output
    assert closed == [True]  # connexion checkpointer fermée aussi sur le chemin nominal


def test_run_waiting_human_prints_resume_hint(monkeypatch: pytest.MonkeyPatch):
    async def fake_ainvoke(state, config):
        return {"status": RunStatus.WAITING_HUMAN, "current_phase": Phase.FICHES}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code == 0
    assert "devaimazing resume" in result.output


def test_run_exports_project_env_before_invoking_graph(monkeypatch: pytest.MonkeyPatch):
    # Régression : les nodes appellent StudioConfig.from_env() en interne
    # (voir leurs docstrings), qui lit DEVAIMAZING_PROJECT depuis
    # os.environ. _load_config() seul (utilisé par la commande CLI pour
    # elle-même) ne suffit pas à le propager — trouvé lors du premier run
    # réel de bout en bout (2026-07-10) : ValueError "DEVAIMAZING_PROJECT
    # non définie" levée par le node pm au moment où le graphe l'invoque.
    seen_env = {}

    async def fake_ainvoke(state, config):
        seen_env["project"] = os.environ.get("DEVAIMAZING_PROJECT")
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code == 0
    assert seen_env["project"] == "demo"


def test_run_closes_checkpointer_connection_even_on_error(monkeypatch: pytest.MonkeyPatch):
    # Régression : build_graph() laisse la connexion SQLite du checkpointer
    # ouverte par conception (voir sa docstring, "à la charge de
    # l'appelant"). Sans fermeture explicite, le process ne se termine
    # jamais (Py_Finalize attend indéfiniment le thread worker aiosqlite) —
    # trouvé lors du premier run réel de bout en bout (2026-07-10) : le
    # process restait bloqué après la fin du run, sans traceback ni
    # message, juste un terminal qui ne rendait jamais la main.
    closed = []

    async def fake_ainvoke(state, config):
        raise RuntimeError("erreur pendant le run")

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer(closed))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code != 0  # l'exception se propage toujours
    assert closed == [True]  # mais la connexion a bien été fermée (finally)


def test_run_prompts_for_objective_when_missing(monkeypatch: pytest.MonkeyPatch):
    async def fake_ainvoke(state, config):
        assert state.objective_raw == "objectif tapé au prompt"
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo"], input="objectif tapé au prompt\n")

    assert result.exit_code == 0


# --- resume ---

def test_resume_not_waiting_prints_warning(monkeypatch: pytest.MonkeyPatch):
    fake_graph = SimpleNamespace(
        aget_state=None, aupdate_state=None, ainvoke=None,
        checkpointer=_fake_checkpointer([]),
    )

    async def fake_aget_state(config):
        return _FakeSnapshot({"awaiting_human_validation": False})

    fake_graph.aget_state = fake_aget_state

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["resume", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert "n'est pas en attente" in result.output


def test_resume_success_clears_flag_and_continues(monkeypatch: pytest.MonkeyPatch):
    state = {"awaiting_human_validation": True, "status": RunStatus.WAITING_HUMAN}
    updated = {}
    closed = []

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fake_aupdate_state(config, updates):
        updated.update(updates)

    async def fake_ainvoke(input_state, config):
        assert input_state is None  # reprise : pas de nouvel état initial
        # Régression : même bug que pour `run`, voir
        # test_run_exports_project_env_before_invoking_graph.
        assert os.environ.get("DEVAIMAZING_PROJECT") == "demo"
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, aupdate_state=fake_aupdate_state, ainvoke=fake_ainvoke,
        checkpointer=_fake_checkpointer(closed),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["resume", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert updated["awaiting_human_validation"] is False
    assert "terminé" in result.output
    assert closed == [True]  # régression : voir test_run_closes_checkpointer_connection_even_on_error


def test_resume_unknown_run_prints_error(monkeypatch: pytest.MonkeyPatch):
    async def fake_aget_state(config):
        return _FakeSnapshot({})

    fake_graph = SimpleNamespace(aget_state=fake_aget_state, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["resume", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert "introuvable" in result.output


# --- runs ---

def test_runs_lists_history_from_project_map(repo: Path):
    project_map = repo / "specs" / "project-map.md"
    project_map.parent.mkdir(parents=True)
    project_map.write_text(
        "## Carte des fichiers\n\n"
        "| Chemin | Rôle | Agent | Run | Contraintes |\n"
        "|---|---|---|---|---|\n"
        "| | | | | |\n\n"
        "## Historique des runs\n\n"
        "| Run ID | Date | Objectif | Statut | Fichiers créés | Fichiers modifiés |\n"
        "|---|---|---|---|---|---|\n"
        "| run-001 | 2026-07-10 | ajout panier | completed | 3 | - |\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["runs", "demo"])

    assert result.exit_code == 0
    assert "run-001" in result.output


def test_runs_no_project_map_prints_warning(repo: Path):
    result = CliRunner().invoke(main, ["runs", "demo"])

    assert result.exit_code == 0
    assert "Aucun project-map.md" in result.output


def test_parse_run_history_table():
    content = (
        "## Historique des runs\n\n"
        "| Run ID | Date | Objectif | Statut | Fichiers créés | Fichiers modifiés |\n"
        "|---|---|---|---|---|---|\n"
        "| run-001 | 2026-07-10 | x | completed | 3 | - |\n"
        "\n## Points de vigilance\n"
    )

    rows = cli_module._parse_run_history_table(content)

    assert rows == [["run-001", "2026-07-10", "x", "completed", "3", "-"]]


# --- metrics ---

def test_metrics_table_format(repo: Path, tmp_path: Path):
    import asyncio
    from studio.metrics import MetricsCollector, TaskMetrics
    from datetime import datetime, timezone

    async def _seed():
        collector = MetricsCollector(tmp_path / "metrics.db")
        await collector.record_task(TaskMetrics(
            task_id="t1", run_id="run-042", card_id="c1", agent="back", phase=4,
            model="qwen2.5:7b-instruct", tokens_prompt=10, tokens_completion=5,
            llm_duration_ms=100, total_duration_ms=150, claude_code_calls=0,
            status="success", iteration=1, created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        ))

    asyncio.run(_seed())

    result = CliRunner().invoke(main, ["metrics", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert "10" in result.output


def test_metrics_unknown_run_prints_error(tmp_path: Path):
    result = CliRunner().invoke(main, ["metrics", "run-inconnu", "--project", "demo"])

    assert result.exit_code == 0
    assert "Run inconnu" in result.output


def test_metrics_json_format(repo: Path, tmp_path: Path):
    import asyncio
    from studio.metrics import MetricsCollector, TaskMetrics
    from datetime import datetime, timezone

    async def _seed():
        collector = MetricsCollector(tmp_path / "metrics.db")
        await collector.record_task(TaskMetrics(
            task_id="t1", run_id="run-042", card_id="c1", agent="back", phase=4,
            model="qwen2.5:7b-instruct", tokens_prompt=10, tokens_completion=5,
            llm_duration_ms=100, total_duration_ms=150, claude_code_calls=0,
            status="success", iteration=1, created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        ))

    asyncio.run(_seed())

    result = CliRunner().invoke(main, ["metrics", "run-042", "--project", "demo", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["run_id"] == "run-042"


# --- projects ---

def test_projects_lists_yml_files(tmp_path: Path):
    result = CliRunner().invoke(main, ["projects"])

    assert result.exit_code == 0
    assert "demo" in result.output


def test_projects_no_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(tmp_path / "inexistant"))

    result = CliRunner().invoke(main, ["projects"])

    assert result.exit_code == 0
    assert "Aucun répertoire" in result.output


# --- doctor ---

def test_doctor_without_project_runs(monkeypatch: pytest.MonkeyPatch):
    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "Claude Code CLI" in result.output
    assert "Git" in result.output
