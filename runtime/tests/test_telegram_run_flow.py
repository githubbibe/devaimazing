"""
Tests de studio.telegram.run_flow (/run <nom_feature>, ADR 0015, Décision 4)
— bot Telegram remplacé par un objet simple qui enregistre les appels (comme
test_telegram_new_project_flow.py), build_graph/queries.fetch_run_state
mockés (comme test_cli.py, pas de vraie base SQLite/LangGraph).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import studio.telegram.run_flow as run_flow_module
import studio.tools.queries as queries_module
from studio.state import RunStatus
from studio.telegram.run_flow import (
    _active_runs,
    handle_run_reply,
    start_run,
)
from studio.tools import planification
from studio.tools.planification import PlanificationEntry

_CHAT_ID = 42
_THREAD_ID = 999
_FEATURE_NAME = "ajout-panier"
_RUN_ID = "run-20260730-101500"
_VALID_CARD = "**Nom de la feature** : ajout-panier\n**Objectif brut** : x\n"


@pytest.fixture(autouse=True)
def _clear_pending_state():
    _active_runs.clear()
    yield
    _active_runs.clear()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    return repo


@pytest.fixture
def config(repo: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repo_path=repo, project_name="demo",
        get=lambda key, default=None: default,
    )


def _write_card(repo: Path, run_id: str = _RUN_ID, content: str = _VALID_CARD) -> None:
    card_path = repo / "specs" / run_id / "card-root.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(content, encoding="utf-8")


async def _seed_entry(config: SimpleNamespace, *, statut: str = "à faire") -> None:
    await planification.upsert_entry(
        config,
        PlanificationEntry(
            feature_name=_FEATURE_NAME, statut=statut, run_id=_RUN_ID,
            content_hash=planification.hash_content(_VALID_CARD),
        ),
    )


class _SentMessage:
    def __init__(self, message_id: int):
        self.message_id = message_id


class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self._next_message_id = 1

    async def send_message(self, chat_id, text, *, message_thread_id=None, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "message_thread_id": message_thread_id})
        message_id = self._next_message_id
        self._next_message_id += 1
        return _SentMessage(message_id)

    async def edit_message_text(self, text, *, chat_id=None, message_id=None):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text})


def _fake_checkpointer(closed: list) -> SimpleNamespace:
    async def _close():
        closed.append(True)

    return SimpleNamespace(conn=SimpleNamespace(close=_close))


def _fake_graph(states: list[dict], closed: list) -> SimpleNamespace:
    """Graphe factice : astream yield `states` dans l'ordre, aupdate_state
    n'est qu'enregistré (pas de vraie persistance SQLite)."""
    update_calls: list = []

    async def fake_astream(initial_state, *, config, stream_mode):
        assert stream_mode == "values"
        for state in states:
            yield state

    async def fake_aupdate_state(thread_config, values):
        update_calls.append((thread_config, values))

    graph = SimpleNamespace(
        astream=fake_astream, aupdate_state=fake_aupdate_state,
        checkpointer=_fake_checkpointer(closed),
    )
    graph.update_calls = update_calls
    return graph


# --- feature inconnue ---

async def test_start_run_unknown_feature_returns_error(config: SimpleNamespace):
    bot = _FakeBot()

    result = await start_run(bot, _CHAT_ID, _THREAD_ID, config, "inconnue")

    assert "error" in result
    assert bot.sent == []


# --- rien à faire (hash identique) ---

async def test_start_run_nothing_to_do_when_hash_matches(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    _write_card(repo)
    await _seed_entry(config, statut="fait")

    async def fake_fetch_run_state(config, run_id):
        return {"status": RunStatus.COMPLETED}

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    async def fail_build_graph(config):
        raise AssertionError("build_graph ne doit pas être appelé (rien à faire)")

    monkeypatch.setattr(run_flow_module, "build_graph", fail_build_graph)

    bot = _FakeBot()
    result = await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)

    assert result == {}
    assert len(bot.sent) == 1
    assert "Rien à implémenter" in bot.sent[0]["text"]
    assert _THREAD_ID not in _active_runs


# --- lancement frais jusqu'à COMPLETED ---

async def test_start_run_fresh_launch_completes_and_updates_planification(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    _write_card(repo)
    await _seed_entry(config)

    async def fake_fetch_run_state(config, run_id):
        return None

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    closed: list = []
    states = [
        {"status": RunStatus.IN_PROGRESS, "current_phase": "AUDIT_AMONT", "agent_sequence": []},
        {
            "status": RunStatus.COMPLETED, "current_phase": "CLOTURE", "agent_sequence": [],
            "card_root_path": f"specs/{_RUN_ID}/card-root.md",
        },
    ]
    graph = _fake_graph(states, closed)

    async def fake_build_graph(config):
        return graph

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph)

    bot = _FakeBot()
    result = await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)
    assert result == {}

    active = _active_runs[_THREAD_ID]
    assert active.task is not None
    await active.task

    assert closed == [True]
    assert len(bot.edits) >= 1
    assert "COMPLETED" in bot.edits[-1]["text"] or "CLOTURE" in bot.edits[-1]["text"]

    entry = await planification.find_entry(config, _FEATURE_NAME)
    assert entry.statut == "fait"
    assert _THREAD_ID not in _active_runs


# --- arrêt WAITING_HUMAN puis reprise ---

async def test_waiting_human_then_reply_resumes(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    _write_card(repo)
    await _seed_entry(config)

    async def fake_fetch_run_state(config, run_id):
        return None

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    closed: list = []
    first_states = [{"status": RunStatus.WAITING_HUMAN, "current_phase": "AUDIT_AMONT", "agent_sequence": []}]
    graph = _fake_graph(first_states, closed)

    async def fake_build_graph(config):
        return graph

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph)

    bot = _FakeBot()
    await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)
    await _active_runs[_THREAD_ID].task

    assert _active_runs[_THREAD_ID].awaiting_human is True

    # Reprise : nouveau graphe factice, states menant à COMPLETED.
    second_states = [
        {
            "status": RunStatus.COMPLETED, "current_phase": "CLOTURE", "agent_sequence": [],
            "card_root_path": f"specs/{_RUN_ID}/card-root.md",
        },
    ]
    graph2 = _fake_graph(second_states, closed)

    async def fake_build_graph_2(config):
        return graph2

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph_2)

    consumed = await handle_run_reply(bot, _CHAT_ID, _THREAD_ID, "n'importe quoi")
    assert consumed is True
    await _active_runs[_THREAD_ID].task

    assert graph2.update_calls == [
        (
            {"configurable": {"thread_id": _RUN_ID}},
            {"status": RunStatus.IN_PROGRESS, "awaiting_human_validation": False},
        )
    ]
    entry = await planification.find_entry(config, _FEATURE_NAME)
    assert entry.statut == "fait"
    assert _THREAD_ID not in _active_runs


# --- FAILED rapporté sans reprise auto ---

async def test_start_run_reports_failed_without_auto_retry(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    _write_card(repo)
    await _seed_entry(config, statut="en cours")

    async def fake_fetch_run_state(config, run_id):
        return {
            "status": RunStatus.FAILED, "current_phase": "TESTS", "agent_sequence": [],
            "requires_manual_intervention": True, "intervention_reason": "back bloqué",
        }

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    async def fail_build_graph(config):
        raise AssertionError("build_graph ne doit pas être appelé (FAILED, pas de reprise auto)")

    monkeypatch.setattr(run_flow_module, "build_graph", fail_build_graph)

    bot = _FakeBot()
    result = await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)

    assert result == {}
    assert len(bot.sent) == 1
    assert "back bloqué" in bot.sent[0]["text"]
    assert _THREAD_ID not in _active_runs


# --- garde anti-double-lancement ---

async def test_start_run_refuses_second_launch_while_active(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    _write_card(repo)
    await _seed_entry(config)

    class _PendingTask:
        def done(self):
            return False

    _active_runs[_THREAD_ID] = run_flow_module._RunState(
        config=config, run_id=_RUN_ID, feature_name=_FEATURE_NAME,
        chat_id=_CHAT_ID, message_id=1, task=_PendingTask(),
    )

    async def fail_build_graph(config):
        raise AssertionError("build_graph ne doit pas être appelé (run déjà en cours)")

    monkeypatch.setattr(run_flow_module, "build_graph", fail_build_graph)

    bot = _FakeBot()
    result = await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)

    assert result == {}
    assert len(bot.sent) == 1
    assert "déjà en cours" in bot.sent[0]["text"]


# --- rate-limiting de l'édition ---

async def test_progress_edits_are_rate_limited_and_flush_final_state(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    _write_card(repo)
    await _seed_entry(config)

    async def fake_fetch_run_state(config, run_id):
        return None

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    closed: list = []
    states = [
        {"status": RunStatus.IN_PROGRESS, "current_phase": f"PHASE_{i}", "agent_sequence": []}
        for i in range(4)
    ] + [
        {
            "status": RunStatus.COMPLETED, "current_phase": "CLOTURE", "agent_sequence": [],
            "card_root_path": f"specs/{_RUN_ID}/card-root.md",
        },
    ]
    graph = _fake_graph(states, closed)

    async def fake_build_graph(config):
        return graph

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph)

    # Horloge mockée : toujours la même valeur -> tous les états arrivent
    # "en même temps" du point de vue du rate-limiting, sauf le flush final
    # (systématique, indépendant du timing).
    monkeypatch.setattr(run_flow_module.time, "monotonic", lambda: 100.0)

    bot = _FakeBot()
    await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)
    await _active_runs[_THREAD_ID].task

    # Un seul edit intermédiaire (le tout premier, last_edit_ts initialisé à
    # 0.0) + le flush final obligatoire.
    assert len(bot.edits) == 2
    assert "CLOTURE" in bot.edits[-1]["text"]
