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
from studio.state import AgentResult, Phase, RunStatus

# Référence captée avant tout monkeypatch : l'fixture autouse _env remplace
# cli_module._ensure_healthy_environment par un faux qui retourne toujours
# True (pour ne pas bloquer les tests run/resume/retry existants sur un
# vrai Ollama/Claude Code CLI) — les tests qui veulent exercer la vraie
# implémentation appellent cette référence directement plutôt que
# cli_module._ensure_healthy_environment.
_real_ensure_healthy_environment = cli_module._ensure_healthy_environment


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

    async def fake_checkout_branch(repo_path, branch):
        pass

    monkeypatch.setattr(cli_module, "checkout_branch", fake_checkout_branch)

    async def fake_ensure_healthy_environment(config):
        return True

    monkeypatch.setattr(cli_module, "_ensure_healthy_environment", fake_ensure_healthy_environment)
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

def test_run_checks_out_base_branch_before_building_graph(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    # Régression : les phases 1/2 commitent directement sur la branche
    # courante (create_run_branch, qui bascule sur base_branch, n'est
    # appelée qu'en fin de phase 3). Sans ce checkout explicite au tout
    # début du run, un repo laissé sur la branche d'un run précédent fait
    # atterrir ces commits ailleurs que sur base_branch — perdus dès que
    # create_run_branch rebascule dessus pour créer la nouvelle branche.
    # Trouvé en run réel (2026-07-11) : architect-brief.md introuvable en
    # phase 5, voir docs/roadmap.md.
    calls = []

    async def fake_checkout_branch(repo_path, branch):
        calls.append((repo_path, branch))

    async def fake_ainvoke(state, config):
        assert calls == [(repo, "develop")]  # checkout déjà fait avant build_graph
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        assert calls == [(repo, "develop")]  # et avant build_graph aussi
        return fake_graph

    monkeypatch.setattr(cli_module, "checkout_branch", fake_checkout_branch)
    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code == 0
    assert calls == [(repo, "develop")]


def test_run_dry_run_does_not_checkout_branch(monkeypatch: pytest.MonkeyPatch):
    async def fail_checkout_branch(repo_path, branch):
        raise AssertionError("checkout_branch ne doit pas être appelé en --dry-run")

    monkeypatch.setattr(cli_module, "checkout_branch", fail_checkout_branch)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x", "--dry-run"])

    assert result.exit_code == 0


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


def test_run_prints_execution_started_before_invoking_graph(monkeypatch: pytest.MonkeyPatch):
    # Sans ce message, le terminal reste silencieux (aucun retour tant
    # qu'un agent n'a pas terminé, plusieurs minutes possible sur un modèle
    # local en CPU) — trouvé en usage réel (2026-07-16, voir
    # docs/roadmap.md), confondu avec un process figé.
    async def fake_ainvoke(state, config):
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code == 0
    assert "Exécution en cours" in result.output
    assert "trace.jsonl" in result.output


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


def test_run_waiting_human_prints_feedback_when_present(monkeypatch: pytest.MonkeyPatch):
    feedback_text = "Aucun bloc <<<DEVAIMAZING_FILE>>> reconnu dans la réponse du PM"

    async def fake_ainvoke(state, config):
        return {
            "status": RunStatus.WAITING_HUMAN,
            "current_phase": Phase.FICHES,
            "agent_results": [
                AgentResult(
                    agent="pm", phase=Phase.FICHES, status="feedback_sent", feedback=feedback_text,
                )
            ],
        }

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code == 0
    assert feedback_text in result.output


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
    # ValueError plutôt que RuntimeError/TimeoutError : ceux-ci sont
    # désormais attrapés proprement par _run_async (voir
    # test_run_external_service_error_prints_clean_message_and_closes) —
    # ce test veut vérifier le cas d'une exception qui se propage toujours.
    closed = []

    async def fake_ainvoke(state, config):
        raise ValueError("bug interne inattendu")

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer(closed))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code != 0  # l'exception se propage toujours
    assert closed == [True]  # mais la connexion a bien été fermée (finally)


def test_run_external_service_error_prints_clean_message_and_closes(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
):
    # TimeoutError/ExternalServiceError/RuntimeError levées pendant
    # graph.ainvoke (Ollama, Claude Code CLI, Git) sont attrapées et
    # affichées proprement plutôt que de laisser remonter la traceback
    # brute à travers LangGraph/httpx/httpcore — vécu en run réel
    # (2026-07-16, voir docs/roadmap.md).
    closed = []

    async def fake_ainvoke(state, config):
        raise TimeoutError(
            "Ollama n'a pas répondu dans le délai imparti (120s) pour le modèle "
            "'qwen2.5:7b-instruct'"
        )

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer(closed))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code == 0
    assert result.exception is None
    assert "Ollama n'a pas répondu" in result.output
    assert "Traceback" not in result.output
    assert closed == [True]

    # Régression : sans marqueur de fin, un run interrompu par une erreur
    # externe n'a qu'un run_start dans trace.jsonl — trouvé lors de ce
    # correctif (2026-07-16, voir docs/roadmap.md).
    run_id = [
        line for line in result.output.splitlines() if line.startswith("Run run-")
    ][0].split()[1]
    trace_path = repo / "specs" / run_id / "trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in events] == ["run_start", "run_end"]
    assert events[-1]["status"] == "interrupted"
    assert "Ollama n'a pas répondu" in events[-1]["error"]


def test_run_prompts_for_objective_when_missing(monkeypatch: pytest.MonkeyPatch):
    async def fake_ainvoke(state, config):
        assert state.objective_raw == "objectif tapé au prompt"
        assert state.imported_brief_content is None
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    # Ligne vide en premier : réponse par défaut ("non") au nouveau prompt
    # d'import de fiche projet, avant l'objectif.
    result = CliRunner().invoke(main, ["run", "demo"], input="\nobjectif tapé au prompt\n")

    assert result.exit_code == 0


def test_run_import_declined_falls_through_to_objective_prompt(monkeypatch: pytest.MonkeyPatch):
    async def fake_ainvoke(state, config):
        assert state.objective_raw == "objectif tapé au prompt"
        assert state.imported_brief_content is None
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo"], input="n\nobjectif tapé au prompt\n")

    assert result.exit_code == 0


def test_run_import_accepted_reads_file_and_passes_content_into_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    brief_file = tmp_path / "brief.md"
    brief_file.write_text("**Nom de la feature** : import-panier\n## Contexte\n...\n", encoding="utf-8")

    async def fake_ainvoke(state, config):
        assert state.imported_brief_content == brief_file.read_text(encoding="utf-8")
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]))

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["run", "demo"], input=f"o\n{brief_file}\n")

    assert result.exit_code == 0


def test_run_import_missing_file_aborts_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    async def fail_build_graph(config):
        raise AssertionError("build_graph ne doit pas être appelé si le fichier importé est introuvable")

    monkeypatch.setattr(cli_module, "build_graph", fail_build_graph)

    missing_path = tmp_path / "inexistant.md"
    result = CliRunner().invoke(main, ["run", "demo"], input=f"o\n{missing_path}\n")

    assert result.exit_code == 0
    assert "introuvable" in result.output


# --- préflight environnement (run/resume/retry) ---

def test_run_aborts_before_build_graph_if_environment_unhealthy(monkeypatch: pytest.MonkeyPatch):
    async def fake_unhealthy(config):
        return False

    async def fail_build_graph(config):
        raise AssertionError("build_graph ne doit pas être appelé si l'environnement n'est pas prêt")

    monkeypatch.setattr(cli_module, "_ensure_healthy_environment", fake_unhealthy)
    monkeypatch.setattr(cli_module, "build_graph", fail_build_graph)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x"])

    assert result.exit_code == 0


def test_run_dry_run_skips_environment_check(monkeypatch: pytest.MonkeyPatch):
    async def fail_ensure_healthy(config):
        raise AssertionError("--dry-run ne doit pas déclencher le préflight (aucun agent exécuté)")

    monkeypatch.setattr(cli_module, "_ensure_healthy_environment", fail_ensure_healthy)

    result = CliRunner().invoke(main, ["run", "demo", "--objective", "x", "--dry-run"])

    assert result.exit_code == 0


def test_resume_aborts_before_build_graph_if_environment_unhealthy(monkeypatch: pytest.MonkeyPatch):
    async def fake_unhealthy(config):
        return False

    async def fail_build_graph(config):
        raise AssertionError("build_graph ne doit pas être appelé si l'environnement n'est pas prêt")

    monkeypatch.setattr(cli_module, "_ensure_healthy_environment", fake_unhealthy)
    monkeypatch.setattr(cli_module, "build_graph", fail_build_graph)

    result = CliRunner().invoke(main, ["resume", "run-042", "--project", "demo"])

    assert result.exit_code == 0


def test_retry_aborts_before_build_graph_if_environment_unhealthy(monkeypatch: pytest.MonkeyPatch):
    async def fake_unhealthy(config):
        return False

    async def fail_build_graph(config):
        raise AssertionError("build_graph ne doit pas être appelé si l'environnement n'est pas prêt")

    monkeypatch.setattr(cli_module, "_ensure_healthy_environment", fake_unhealthy)
    monkeypatch.setattr(cli_module, "build_graph", fail_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"])

    assert result.exit_code == 0


async def test_ensure_healthy_environment_true_when_all_checks_pass(monkeypatch: pytest.MonkeyPatch):
    async def fake_checks(config):
        return [("Claude Code CLI", True, "/usr/bin/claude"), ("Ollama", True, "http://localhost:11434")]

    monkeypatch.setattr(cli_module, "_project_health_checks", fake_checks)

    assert await _real_ensure_healthy_environment(config=SimpleNamespace()) is True


async def test_ensure_healthy_environment_false_and_reports_each_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_checks(config):
        return [
            ("Claude Code CLI", True, "/usr/bin/claude"),
            ("Ollama", False, "http://localhost:11434 injoignable"),
        ]

    monkeypatch.setattr(cli_module, "_project_health_checks", fake_checks)

    result = await _real_ensure_healthy_environment(config=SimpleNamespace())

    assert result is False


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
    assert "Exécution en cours" in result.output  # voir test_run_prints_execution_started_...


def test_resume_external_service_error_prints_clean_message_and_closes(
    monkeypatch: pytest.MonkeyPatch,
):
    state = {"awaiting_human_validation": True, "status": RunStatus.WAITING_HUMAN}
    closed = []

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fake_aupdate_state(config, updates):
        pass

    async def fake_ainvoke(input_state, config):
        raise cli_module.ExternalServiceError("Ollama injoignable après 3 tentatives")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, aupdate_state=fake_aupdate_state, ainvoke=fake_ainvoke,
        checkpointer=_fake_checkpointer(closed),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["resume", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert result.exception is None
    assert "Ollama injoignable" in result.output
    assert "Traceback" not in result.output
    assert closed == [True]


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


# --- retry ---

def test_retry_run_not_found(monkeypatch: pytest.MonkeyPatch):
    async def fake_aget_state(config):
        return _FakeSnapshot({})

    async def fail_ainvoke(state, config):
        raise AssertionError("ainvoke ne doit pas être appelé si le run est introuvable")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert "introuvable" in result.output


def test_retry_refuses_awaiting_human_validation(monkeypatch: pytest.MonkeyPatch):
    async def fake_aget_state(config):
        return _FakeSnapshot({
            "awaiting_human_validation": True, "status": RunStatus.WAITING_HUMAN,
        })

    async def fail_ainvoke(state, config):
        raise AssertionError("ainvoke ne doit pas être appelé, run en attente de validation")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert "devaimazing resume" in result.output


def test_retry_refuses_completed(monkeypatch: pytest.MonkeyPatch):
    async def fake_aget_state(config):
        return _FakeSnapshot({"awaiting_human_validation": False, "status": RunStatus.COMPLETED})

    async def fail_ainvoke(state, config):
        raise AssertionError("ainvoke ne doit pas être appelé, run déjà terminé")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert "rien à rejouer" in result.output


def test_retry_refuses_failed(monkeypatch: pytest.MonkeyPatch):
    async def fake_aget_state(config):
        return _FakeSnapshot({"awaiting_human_validation": False, "status": RunStatus.FAILED})

    async def fail_ainvoke(state, config):
        raise AssertionError("ainvoke ne doit pas être appelé, run déjà échoué")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert "rien à rejouer" in result.output


def test_retry_refuses_pending(monkeypatch: pytest.MonkeyPatch):
    # Garde-fou : ce statut n'a normalement pas de checkpoint exploitable,
    # mais retry doit refuser proprement plutôt que de tenter un ainvoke.
    async def fake_aget_state(config):
        return _FakeSnapshot({"awaiting_human_validation": False, "status": RunStatus.PENDING})

    async def fail_ainvoke(state, config):
        raise AssertionError("ainvoke ne doit pas être appelé pour un run PENDING")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert "rien à rejouer" in result.output


def test_retry_diagnostic_displayed_before_confirmation(monkeypatch: pytest.MonkeyPatch):
    state = {
        "awaiting_human_validation": False,
        "status": RunStatus.IN_PROGRESS,
        "current_phase": Phase.STUBS,
        "agent_sequence": ["back", "back-tu", "test", "secu"],
        "current_agent_index": 1,
        "agent_results": [
            AgentResult(agent="back", phase=Phase.STUBS, status="success", iteration=1),
        ],
    }

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fail_ainvoke(input_state, config):
        raise AssertionError("ainvoke ne doit pas être appelé sans confirmation")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    # Confirmation refusée (défaut de click.confirm) : "\n" équivaut à répondre non.
    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"], input="\n")

    assert result.exit_code == 0
    assert "Diagnostic" in result.output
    assert "back-tu" in result.output  # agent courant (index 1)
    assert "back" in result.output  # dernier résultat


def test_retry_diagnostic_unknown_agent_index(monkeypatch: pytest.MonkeyPatch):
    state = {
        "awaiting_human_validation": False,
        "status": RunStatus.IN_PROGRESS,
        "current_phase": Phase.STUBS,
        "agent_sequence": ["back"],
        "current_agent_index": 5,  # hors bornes
        "agent_results": [],
    }

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fail_ainvoke(input_state, config):
        raise AssertionError("ainvoke ne doit pas être appelé sans confirmation")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"], input="\n")

    assert result.exit_code == 0  # pas d'IndexError
    assert "inconnu" in result.output


def test_retry_confirmation_declined_does_not_invoke(monkeypatch: pytest.MonkeyPatch):
    closed = []
    state = {
        "awaiting_human_validation": False, "status": RunStatus.IN_PROGRESS,
        "current_phase": Phase.STUBS, "agent_sequence": [], "current_agent_index": 0,
        "agent_results": [],
    }

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fail_ainvoke(input_state, config):
        raise AssertionError("ainvoke ne doit pas être appelé si la confirmation est refusée")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer(closed),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"], input="n\n")

    assert result.exit_code == 0
    assert "annulé" in result.output
    assert closed == [True]  # connexion fermée même en cas de refus


def test_retry_confirmation_accepted_invokes_graph(monkeypatch: pytest.MonkeyPatch):
    state = {
        "awaiting_human_validation": False, "status": RunStatus.IN_PROGRESS,
        "current_phase": Phase.STUBS, "agent_sequence": ["back"], "current_agent_index": 0,
        "agent_results": [],
    }
    calls = []

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fake_ainvoke(input_state, config):
        calls.append(input_state)
        assert input_state is None  # reprise : pas de nouvel état initial, comme resume
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"], input="y\n")

    assert result.exit_code == 0
    assert calls == [None]
    assert "terminé" in result.output
    assert "Exécution en cours" in result.output  # voir test_run_prints_execution_started_...


def test_retry_external_service_error_prints_clean_message_and_closes(
    monkeypatch: pytest.MonkeyPatch,
):
    state = {
        "awaiting_human_validation": False, "status": RunStatus.IN_PROGRESS,
        "current_phase": Phase.STUBS, "agent_sequence": ["back"], "current_agent_index": 0,
        "agent_results": [],
    }
    closed = []

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fake_ainvoke(input_state, config):
        raise RuntimeError("Commande git échouée (code 1) : git checkout develop")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer(closed),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"], input="y\n")

    assert result.exit_code == 0
    assert result.exception is None
    assert "Commande git échouée" in result.output
    assert "Traceback" not in result.output
    assert closed == [True]


def test_retry_closes_checkpointer_connection_on_success(monkeypatch: pytest.MonkeyPatch):
    closed = []
    state = {
        "awaiting_human_validation": False, "status": RunStatus.IN_PROGRESS,
        "current_phase": Phase.STUBS, "agent_sequence": [], "current_agent_index": 0,
        "agent_results": [],
    }

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fake_ainvoke(input_state, config):
        return {"status": RunStatus.COMPLETED}

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fake_ainvoke, checkpointer=_fake_checkpointer(closed),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"], input="y\n")

    assert result.exit_code == 0
    assert closed == [True]


def test_retry_closes_checkpointer_connection_on_refusal(monkeypatch: pytest.MonkeyPatch):
    closed = []

    async def fake_aget_state(config):
        return _FakeSnapshot({"awaiting_human_validation": False, "status": RunStatus.COMPLETED})

    async def fail_ainvoke(state, config):
        raise AssertionError("ainvoke ne doit pas être appelé, run déjà terminé")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer(closed),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"])

    assert result.exit_code == 0
    assert closed == [True]  # fermée même dans un cas de refus d'éligibilité (pas seulement confirmation)


def test_retry_shows_manual_intervention_reason(monkeypatch: pytest.MonkeyPatch):
    state = {
        "awaiting_human_validation": False, "status": RunStatus.IN_PROGRESS,
        "current_phase": Phase.AUDIT_STUBS, "agent_sequence": ["back"], "current_agent_index": 0,
        "agent_results": [],
        "requires_manual_intervention": True,
        "intervention_reason": "conflit de merge non résolu",
    }

    async def fake_aget_state(config):
        return _FakeSnapshot(dict(state))

    async def fail_ainvoke(input_state, config):
        raise AssertionError("ainvoke ne doit pas être appelé sans confirmation")

    fake_graph = SimpleNamespace(
        aget_state=fake_aget_state, ainvoke=fail_ainvoke, checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return fake_graph

    monkeypatch.setattr(cli_module, "build_graph", fake_build_graph)

    result = CliRunner().invoke(main, ["retry", "run-042", "--project", "demo"], input="\n")

    assert result.exit_code == 0
    assert "conflit de merge non résolu" in result.output


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


# --- run-agent ---

def _write_card(repo: Path, run_id: str, name: str, content: str = "# fiche") -> None:
    path = repo / "specs" / run_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_run_agent_never_builds_graph(monkeypatch: pytest.MonkeyPatch, repo: Path):
    # Garde-fou central de la commande : jamais de build_graph/state.db,
    # contrairement à run/resume/retry (voir docstring de run_agent).
    async def fail_build_graph(config):
        raise AssertionError("run-agent ne doit jamais construire le graphe LangGraph")

    monkeypatch.setattr(cli_module, "build_graph", fail_build_graph)

    async def fake_backend_run(state):
        return {"agent_results": []}

    monkeypatch.setattr(cli_module.backend, "run", fake_backend_run)

    result = CliRunner().invoke(
        main, ["run-agent", "demo", "run-001", "back", "--phase", "STUBS"]
    )

    assert result.exit_code == 0
    assert "state.db" in result.output


def test_run_agent_discovers_cards_from_disk(monkeypatch: pytest.MonkeyPatch, repo: Path):
    _write_card(repo, "run-001", "card-root.md")
    _write_card(repo, "run-001", "architect-brief.md")
    _write_card(repo, "run-001", "back.md")
    _write_card(repo, "run-001", "front.md")

    captured = {}

    async def fake_backend_run(state):
        captured["state"] = state
        return {"agent_results": []}

    monkeypatch.setattr(cli_module.backend, "run", fake_backend_run)

    result = CliRunner().invoke(
        main,
        [
            "run-agent", "demo", "run-001", "back", "--phase", "STUBS",
            "--existing-file", "backend/database.py",
        ],
    )

    assert result.exit_code == 0
    state = captured["state"]
    assert state.agent_cards == {
        "back": "specs/run-001/back.md", "front": "specs/run-001/front.md",
    }
    assert state.agent_sequence == ["back", "front"]
    assert state.current_agent_index == 0
    assert state.card_root_path == "specs/run-001/card-root.md"
    assert state.architect_brief_path == "specs/run-001/architect-brief.md"
    assert state.agent_card_metadata["back"]["existing_files_to_read"] == ["backend/database.py"]
    assert state.agent_card_metadata["front"]["existing_files_to_read"] == []
    assert state.run_id == "run-001"
    assert state.status == RunStatus.IN_PROGRESS


def test_run_agent_card_override_bypasses_discovery(monkeypatch: pytest.MonkeyPatch, repo: Path):
    # Fiche à un chemin non conventionnel (pas specs/<run-id>/back.md) :
    # --card doit primer sur la découverte automatique (absente ici).
    (repo / "elsewhere.md").parent.mkdir(parents=True, exist_ok=True)
    (repo / "elsewhere.md").write_text("# fiche", encoding="utf-8")

    captured = {}

    async def fake_backend_run(state):
        captured["state"] = state
        return {"agent_results": []}

    monkeypatch.setattr(cli_module.backend, "run", fake_backend_run)

    result = CliRunner().invoke(
        main,
        ["run-agent", "demo", "run-001", "back", "--phase", "STUBS", "--card", "elsewhere.md"],
    )

    assert result.exit_code == 0
    assert captured["state"].agent_cards == {"back": "elsewhere.md"}
    assert captured["state"].agent_sequence == ["back"]
    assert captured["state"].current_agent_index == 0


@pytest.mark.parametrize(
    "agent,node_attr",
    [
        ("back", "backend"),
        ("back-tu", "backend"),
        ("front", "frontend"),
        ("front-tu", "frontend"),
        ("test", "test_node"),
        ("secu", "security"),
        ("architect", "architect"),
        ("pm", "pm"),
        ("closer", "closer"),
    ],
)
def test_run_agent_dispatches_to_correct_node(
    monkeypatch: pytest.MonkeyPatch, repo: Path, agent: str, node_attr: str
):
    calls = []

    async def fake_run(state):
        calls.append(state)
        return {"agent_results": []}

    monkeypatch.setattr(getattr(cli_module, node_attr), "run", fake_run)

    extra = ["--objective", "x"] if agent == "pm" else []
    if agent == "closer":
        extra += ["--branch-name", "studio/x"]
    phase = "FICHES" if agent == "pm" else "STUBS"

    result = CliRunner().invoke(
        main, ["run-agent", "demo", "run-001", agent, "--phase", phase] + extra
    )

    assert result.exit_code == 0
    assert len(calls) == 1


def test_run_agent_pm_prompts_for_objective_when_missing(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    captured = {}

    async def fake_pm_run(state):
        captured["state"] = state
        return {"agent_results": []}

    monkeypatch.setattr(cli_module.pm, "run", fake_pm_run)

    result = CliRunner().invoke(
        main, ["run-agent", "demo", "run-001", "pm", "--phase", "CADRAGE"], input="mon objectif\n"
    )

    assert result.exit_code == 0
    assert captured["state"].objective_raw == "mon objectif"


def test_run_agent_branch_name_forwarded_to_closer(monkeypatch: pytest.MonkeyPatch, repo: Path):
    captured = {}

    async def fake_closer_run(state):
        captured["state"] = state
        return {"status": RunStatus.COMPLETED}

    monkeypatch.setattr(cli_module.closer, "run", fake_closer_run)

    result = CliRunner().invoke(
        main,
        ["run-agent", "demo", "run-001", "closer", "--phase", "CLOTURE",
         "--branch-name", "studio/ajout-panier-a3f9c"],
    )

    assert result.exit_code == 0
    assert captured["state"].branch_name == "studio/ajout-panier-a3f9c"


def test_run_agent_prints_updates_returned_by_node(monkeypatch: pytest.MonkeyPatch, repo: Path):
    async def fake_backend_run(state):
        return {"current_phase": Phase.AUDIT_STUBS, "current_agent_index": 0}

    monkeypatch.setattr(cli_module.backend, "run", fake_backend_run)

    result = CliRunner().invoke(
        main, ["run-agent", "demo", "run-001", "back", "--phase", "STUBS"]
    )

    assert result.exit_code == 0
    assert "current_phase" in result.output
    assert "AUDIT_STUBS" in result.output
    # Même message que run/resume/retry (voir
    # test_run_prints_execution_started_before_invoking_graph) : node.run
    # peut appeler Ollama/Claude Code CLI, tout aussi silencieux sinon.
    assert "Exécution en cours" in result.output
    assert "trace.jsonl" in result.output


def test_run_agent_reports_node_exception_without_traceback(repo: Path):
    # Aucun mock : architect.run lève un KeyError réel pour une phase qu'il
    # ne gère pas (voir studio.nodes.architect.run) — vérifie que run-agent
    # l'affiche proprement plutôt que de crasher avec une trace complète.
    result = CliRunner().invoke(
        main, ["run-agent", "demo", "run-001", "architect", "--phase", "STUBS"]
    )

    assert result.exit_code == 0
    assert "Phase non gérée" in result.output
    assert result.exception is None


# --- run-agent --reference-dir ---

def test_run_agent_reference_dir_reports_match(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
):
    async def fake_backend_run(state):
        (repo / "specs" / "run-001").mkdir(parents=True, exist_ok=True)
        (repo / "specs" / "run-001" / "back.md").write_text("contenu identique", encoding="utf-8")
        return {
            "agent_results": [
                AgentResult(
                    agent="back", phase=Phase.STUBS, status="success",
                    output_files=["specs/run-001/back.md"],
                )
            ]
        }

    monkeypatch.setattr(cli_module.backend, "run", fake_backend_run)

    reference_dir = tmp_path / "reference"
    (reference_dir / "specs" / "run-001").mkdir(parents=True)
    (reference_dir / "specs" / "run-001" / "back.md").write_text(
        "contenu identique", encoding="utf-8"
    )

    result = CliRunner().invoke(
        main,
        [
            "run-agent", "demo", "run-001", "back", "--phase", "STUBS",
            "--reference-dir", str(reference_dir),
        ],
    )

    assert result.exit_code == 0
    assert "identique à la référence" in result.output


def test_run_agent_reference_dir_reports_diff(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
):
    async def fake_backend_run(state):
        (repo / "specs" / "run-001").mkdir(parents=True, exist_ok=True)
        (repo / "specs" / "run-001" / "back.md").write_text("contenu produit", encoding="utf-8")
        return {
            "agent_results": [
                AgentResult(
                    agent="back", phase=Phase.STUBS, status="success",
                    output_files=["specs/run-001/back.md"],
                )
            ]
        }

    monkeypatch.setattr(cli_module.backend, "run", fake_backend_run)

    reference_dir = tmp_path / "reference"
    (reference_dir / "specs" / "run-001").mkdir(parents=True)
    (reference_dir / "specs" / "run-001" / "back.md").write_text(
        "contenu de référence", encoding="utf-8"
    )

    result = CliRunner().invoke(
        main,
        [
            "run-agent", "demo", "run-001", "back", "--phase", "STUBS",
            "--reference-dir", str(reference_dir),
        ],
    )

    assert result.exit_code == 0
    assert "diffère de la référence" in result.output
    assert "-contenu de référence" in result.output
    assert "+contenu produit" in result.output


def test_run_agent_reference_dir_missing_reference_file(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
):
    async def fake_backend_run(state):
        (repo / "specs" / "run-001").mkdir(parents=True, exist_ok=True)
        (repo / "specs" / "run-001" / "back.md").write_text("contenu produit", encoding="utf-8")
        return {
            "agent_results": [
                AgentResult(
                    agent="back", phase=Phase.STUBS, status="success",
                    output_files=["specs/run-001/back.md"],
                )
            ]
        }

    monkeypatch.setattr(cli_module.backend, "run", fake_backend_run)

    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()

    result = CliRunner().invoke(
        main,
        [
            "run-agent", "demo", "run-001", "back", "--phase", "STUBS",
            "--reference-dir", str(reference_dir),
        ],
    )

    assert result.exit_code == 0
    assert "référence absente" in result.output


def test_run_agent_reference_dir_no_output_files_prints_note(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
):
    async def fake_architect_run(state):
        return {"current_phase": Phase.IMPLEMENTATION, "current_agent_index": 0}

    monkeypatch.setattr(cli_module.architect, "run", fake_architect_run)

    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()

    result = CliRunner().invoke(
        main,
        [
            "run-agent", "demo", "run-001", "architect", "--phase", "AUDIT_STUBS",
            "--reference-dir", str(reference_dir),
        ],
    )

    assert result.exit_code == 0
    assert "rien à comparer" in result.output


def test_run_agent_without_reference_dir_skips_comparison(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    async def fake_backend_run(state):
        return {
            "agent_results": [
                AgentResult(agent="back", phase=Phase.STUBS, status="success", output_files=[])
            ]
        }

    monkeypatch.setattr(cli_module.backend, "run", fake_backend_run)

    result = CliRunner().invoke(
        main, ["run-agent", "demo", "run-001", "back", "--phase", "STUBS"]
    )

    assert result.exit_code == 0
    assert "référence" not in result.output


# --- new-project ---


@pytest.fixture
def fake_studio_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Racine devaimazing factice sous tmp_path (jamais la vraie racine du repo) :
    new-project ne doit jamais créer de dossier à côté du vrai checkout
    pendant les tests. Le vrai template est copié tel quel pour vérifier le
    comportement réel de substitution.
    """
    root = tmp_path / "devaimazing"
    templates_dir = root / "templates"
    templates_dir.mkdir(parents=True)
    real_template = (
        Path(__file__).resolve().parents[2] / "templates" / "project-config.yml.template"
    )
    (templates_dir / "project-config.yml.template").write_text(
        real_template.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(cli_module, "_devaimazing_root", lambda: root)
    return root


def _config_path(tmp_path: Path, name: str) -> Path:
    return tmp_path / "config" / "projects" / f"{name}.yml"


async def _fail_if_called(*args, **kwargs):
    raise AssertionError("ne devrait pas être appelé")


def test_new_project_creates_repo_and_config(
    monkeypatch: pytest.MonkeyPatch, fake_studio_root: Path, tmp_path: Path
):
    result = CliRunner().invoke(main, ["new-project", "mon-projet", "--skip-github"])

    assert result.exit_code == 0
    target = fake_studio_root.parent / "mon-projet"
    assert (target / ".git").is_dir()
    assert (target / "README.md").read_text(encoding="utf-8") == "# mon-projet\n"

    config_path = _config_path(tmp_path, "mon-projet")
    assert config_path.is_file()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["name"] == "mon-projet"
    assert config["repo_path"] == str(target)
    # Même logique que run/resume/retry/run-agent : un message avant toute
    # opération qui pourrait sembler figée (ici git init, rapide en
    # pratique, mais le silence total avant "Repo Git initialisé" restait
    # ambigu — voir test_run_prints_execution_started_before_invoking_graph).
    assert "Initialisation du repo Git" in result.output


def test_new_project_config_already_exists_is_noop(
    monkeypatch: pytest.MonkeyPatch, fake_studio_root: Path, tmp_path: Path
):
    config_path = _config_path(tmp_path, "mon-projet")
    _write_yaml(config_path, {"name": "mon-projet", "repo_path": "/déjà/là"})
    monkeypatch.setattr(cli_module, "init_repo", _fail_if_called)

    result = CliRunner().invoke(main, ["new-project", "mon-projet", "--skip-github"])

    assert result.exit_code == 0
    assert "existe déjà" in result.output
    assert not (fake_studio_root.parent / "mon-projet").exists()


def test_new_project_reuses_existing_git_repo(
    monkeypatch: pytest.MonkeyPatch, fake_studio_root: Path, tmp_path: Path
):
    target = fake_studio_root.parent / "mon-projet"
    (target / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli_module, "init_repo", _fail_if_called)
    monkeypatch.setattr(cli_module, "create_initial_commit", _fail_if_called)

    result = CliRunner().invoke(main, ["new-project", "mon-projet", "--skip-github"])

    assert result.exit_code == 0
    config_path = _config_path(tmp_path, "mon-projet")
    assert config_path.is_file()


def test_new_project_target_exists_not_git_repo_prints_error(
    fake_studio_root: Path, tmp_path: Path
):
    target = fake_studio_root.parent / "mon-projet"
    target.mkdir(parents=True)
    (target / "fichier.txt").write_text("contenu", encoding="utf-8")

    result = CliRunner().invoke(main, ["new-project", "mon-projet", "--skip-github"])

    assert "n'est pas un repo Git" in result.output
    assert not _config_path(tmp_path, "mon-projet").exists()


def test_new_project_gh_missing_warns_but_still_writes_config(
    monkeypatch: pytest.MonkeyPatch, fake_studio_root: Path, tmp_path: Path
):
    monkeypatch.setattr(cli_module, "_gh_available", lambda: False)
    monkeypatch.setattr(cli_module, "create_github_remote", _fail_if_called)

    result = CliRunner().invoke(main, ["new-project", "mon-projet"])

    assert result.exit_code == 0
    assert "gh introuvable" in result.output
    assert _config_path(tmp_path, "mon-projet").is_file()


def test_new_project_confirmation_declined_skips_github(
    monkeypatch: pytest.MonkeyPatch, fake_studio_root: Path, tmp_path: Path
):
    monkeypatch.setattr(cli_module, "_gh_available", lambda: True)
    monkeypatch.setattr(cli_module, "create_github_remote", _fail_if_called)
    monkeypatch.setattr(cli_module, "push_branch", _fail_if_called)

    result = CliRunner().invoke(main, ["new-project", "mon-projet"], input="n\n")

    assert result.exit_code == 0
    assert "non créé" in result.output


def test_new_project_confirmation_accepted_creates_remote_and_pushes(
    monkeypatch: pytest.MonkeyPatch, fake_studio_root: Path, tmp_path: Path
):
    calls: list = []

    async def fake_create_github_remote(repo_path, name, private=True):
        calls.append(("create", repo_path, name, private))

    async def fake_push_branch(repo_path, branch, remote="origin"):
        calls.append(("push", repo_path, branch, remote))

    monkeypatch.setattr(cli_module, "_gh_available", lambda: True)
    monkeypatch.setattr(cli_module, "create_github_remote", fake_create_github_remote)
    monkeypatch.setattr(cli_module, "push_branch", fake_push_branch)

    result = CliRunner().invoke(main, ["new-project", "mon-projet"], input="y\n")

    assert result.exit_code == 0
    target = fake_studio_root.parent / "mon-projet"
    assert calls[0] == ("create", target, "mon-projet", True)
    assert calls[1] == ("push", target, "develop", "origin")
    assert "Création du repo GitHub et push en cours" in result.output


def test_new_project_public_flag_passed_through(
    monkeypatch: pytest.MonkeyPatch, fake_studio_root: Path, tmp_path: Path
):
    calls: list = []

    async def fake_create_github_remote(repo_path, name, private=True):
        calls.append(private)

    async def fake_push_branch(repo_path, branch, remote="origin"):
        pass

    monkeypatch.setattr(cli_module, "_gh_available", lambda: True)
    monkeypatch.setattr(cli_module, "create_github_remote", fake_create_github_remote)
    monkeypatch.setattr(cli_module, "push_branch", fake_push_branch)

    result = CliRunner().invoke(main, ["new-project", "mon-projet", "--public"], input="y\n")

    assert result.exit_code == 0
    assert calls == [False]


def test_new_project_skip_github_never_prompts(
    monkeypatch: pytest.MonkeyPatch, fake_studio_root: Path, tmp_path: Path
):
    monkeypatch.setattr(cli_module, "_gh_available", lambda: True)
    monkeypatch.setattr(cli_module, "create_github_remote", _fail_if_called)
    monkeypatch.setattr(cli_module, "push_branch", _fail_if_called)

    result = CliRunner().invoke(main, ["new-project", "mon-projet", "--skip-github"])

    assert result.exit_code == 0
    assert "--skip-github" in result.output
