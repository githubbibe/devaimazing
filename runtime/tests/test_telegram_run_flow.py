"""
Tests de studio.telegram.run_flow (/run <nom_feature>, ADR 0015, Décision 4)
— bot Telegram remplacé par un objet simple qui enregistre les appels (comme
test_telegram_new_project_flow.py), build_graph/queries.fetch_run_state
mockés (comme test_cli.py, pas de vraie base SQLite/LangGraph).
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import studio.telegram.run_flow as run_flow_module
import studio.tools.queries as queries_module
from studio.config import _current_project_name
from studio.state import AgentResult, Phase, RunStatus
from studio.telegram.run_flow import (
    _active_runs,
    handle_checkpoint_continue_callback,
    handle_run_reply,
    start_run,
    stop_active_run,
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
        repo_path=repo, project_name="demo", config_dir=None,
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
        self.documents: list[dict] = []
        self.edits: list[dict] = []
        self._next_message_id = 1

    async def send_message(self, chat_id, text, *, message_thread_id=None, reply_markup=None):
        self.sent.append({
            "chat_id": chat_id, "text": text, "message_thread_id": message_thread_id,
            "reply_markup": reply_markup,
        })
        message_id = self._next_message_id
        self._next_message_id += 1
        return _SentMessage(message_id)

    async def send_document(self, chat_id, document, *, caption=None, message_thread_id=None):
        self.documents.append({
            "chat_id": chat_id, "filename": document.filename, "caption": caption,
            "message_thread_id": message_thread_id,
        })
        message_id = self._next_message_id
        self._next_message_id += 1
        return _SentMessage(message_id)

    async def edit_message_text(self, text, *, chat_id=None, message_id=None, reply_markup=None):
        self.edits.append({
            "chat_id": chat_id, "message_id": message_id, "text": text,
            "reply_markup": reply_markup,
        })


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
            "merged_commit": "abc123",
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
    # Menu à boutons (ADR 0015, Décision 7) rattaché sur un statut terminal.
    assert bot.edits[-1]["reply_markup"] is not None


async def test_start_run_exposes_project_via_context_var_during_astream(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    """
    Non-régression : les nodes du graphe appellent StudioConfig.from_env()
    en interne (pas de config passée explicitement) — sans project_context()
    posé autour de graph.astream, ils ne verraient aucun projet courant et
    lèveraient ValueError dès le premier node, plantant la tâche de fond en
    silence (bug réel trouvé en run, voir docs/roadmap.md). Ce test
    vérifie directement que le ContextVar est bien positionné pendant
    l'itération d'astream, et remis à None une fois le run terminé.
    """
    _write_card(repo)
    await _seed_entry(config)

    async def fake_fetch_run_state(config, run_id):
        return None

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    seen_project_names: list = []

    async def fake_astream(initial_state, *, config, stream_mode):
        seen_project_names.append(_current_project_name.get())
        yield {
            "status": RunStatus.COMPLETED, "current_phase": "CLOTURE", "agent_sequence": [],
            "card_root_path": f"specs/{_RUN_ID}/card-root.md", "merged_commit": "abc123",
        }

    async def fake_aupdate_state(thread_config, values):
        pass

    graph = SimpleNamespace(
        astream=fake_astream, aupdate_state=fake_aupdate_state,
        checkpointer=_fake_checkpointer([]),
    )

    async def fake_build_graph(config):
        return graph

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph)

    bot = _FakeBot()
    await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)
    await _active_runs[_THREAD_ID].task

    assert seen_project_names == ["demo"]
    assert _current_project_name.get() is None

    entry = await planification.find_entry(config, _FEATURE_NAME)
    assert entry.statut == "fait"
    # Traçabilité de la version de code livrée (voir studio.nodes.closer).
    assert entry.merged_commit == "abc123"
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
    # Pas de menu à boutons sur WAITING_HUMAN (ADR 0015, Décision 7) : le
    # topic attend une réponse pour reprendre, pas un nouveau choix de menu.
    assert bot.edits[-1]["reply_markup"] is None
    # Notification de checkpoint (ADR 0015, révision 2026-08-05) : un
    # NOUVEAU message avec le bouton "Continuer", pas seulement l'édition de
    # progression ci-dessus — aucun agent_results ici, donc pas de pièce
    # jointe, juste le texte + bouton dans le même message.
    checkpoint_message = bot.sent[-1]
    assert checkpoint_message["reply_markup"] is not None
    assert "en pause" in checkpoint_message["text"]
    assert bot.documents == []

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


# --- checkpoint WAITING_HUMAN : pièce jointe + bouton "Continuer" ---

async def test_waiting_human_attaches_last_output_file(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    """
    ADR 0015 (révision 2026-08-05) : le fichier produit par le dernier agent
    (ex. architect-brief.md) doit être joint en pièce jointe au message de
    checkpoint, avec le bouton "Continuer" sur un message texte séparé (voir
    _send_checkpoint_notification — jamais en légende du document,
    edit_message_text ne peut pas éditer un message document).
    """
    _write_card(repo)
    await _seed_entry(config)

    artifact_path = repo / "specs" / _RUN_ID / "architect-brief.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("Audit amont : rien à signaler.\n", encoding="utf-8")

    async def fake_fetch_run_state(config, run_id):
        return None

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    agent_result = AgentResult(
        agent="architect", phase=Phase.AUDIT_AMONT, status="success",
        output_files=[f"specs/{_RUN_ID}/architect-brief.md"],
    )
    states = [{
        "status": RunStatus.WAITING_HUMAN, "current_phase": "FICHES", "agent_sequence": [],
        "agent_results": [agent_result],
    }]
    graph = _fake_graph(states, [])

    async def fake_build_graph(config):
        return graph

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph)

    bot = _FakeBot()
    await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)
    await _active_runs[_THREAD_ID].task

    assert len(bot.documents) == 1
    assert bot.documents[0]["filename"] == "architect-brief.md"
    assert bot.documents[0]["message_thread_id"] == _THREAD_ID

    continue_message = bot.sent[-1]
    assert continue_message["text"] == "Continuer ?"
    assert continue_message["reply_markup"] is not None


# --- checkpoint WAITING_HUMAN : reprise par le bouton "Continuer" ---

async def test_checkpoint_continue_callback_resumes_like_text_reply(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    """Le bouton "Continuer" (handle_checkpoint_continue_callback) doit
    reprendre le run exactement comme un texte tapé (handle_run_reply,
    _resume_waiting_run partagé)."""
    _write_card(repo)
    await _seed_entry(config)

    async def fake_fetch_run_state(config, run_id):
        return None

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    states = [{"status": RunStatus.WAITING_HUMAN, "current_phase": "AUDIT_AMONT", "agent_sequence": []}]
    graph = _fake_graph(states, [])

    async def fake_build_graph(config):
        return graph

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph)

    bot = _FakeBot()
    await start_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)
    await _active_runs[_THREAD_ID].task

    assert _active_runs[_THREAD_ID].awaiting_human is True

    second_states = [{
        "status": RunStatus.COMPLETED, "current_phase": "CLOTURE", "agent_sequence": [],
        "card_root_path": f"specs/{_RUN_ID}/card-root.md",
    }]
    graph2 = _fake_graph(second_states, [])

    async def fake_build_graph_2(config):
        return graph2

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph_2)

    resumed = await handle_checkpoint_continue_callback(bot, _CHAT_ID, _THREAD_ID)
    assert resumed is True
    await _active_runs[_THREAD_ID].task

    entry = await planification.find_entry(config, _FEATURE_NAME)
    assert entry.statut == "fait"

    # Un topic sans run en attente : le bouton ne doit rien déclencher.
    assert await handle_checkpoint_continue_callback(bot, _CHAT_ID, _THREAD_ID) is False


# --- FAILED rapporté avec bouton "Réessayer" (ADR 0015 révision 2026-08-05 bis) ---

async def test_start_run_reports_failed_with_retry_button(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    """/run sur une feature FAILED ne relance rien tout seul (aucun
    build_graph ici), mais propose le bouton "Réessayer" plutôt qu'un
    message plat sans action possible."""
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
    assert bot.sent[0]["reply_markup"] is not None
    assert _THREAD_ID not in _active_runs


# --- reprise d'un run FAILED via le bouton "Réessayer" ---

async def test_retry_failed_run_resets_iteration_budget_and_resumes(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, repo: Path,
):
    """retry_failed_run doit purger les tentatives ratées de l'agent/phase en
    échec (sinon routing.agent_iteration_count rebloque immédiatement, voir
    _reset_failed_state_for_retry) et reprendre le run normalement."""
    _write_card(repo)
    await _seed_entry(config, statut="en cours")

    failed_result = AgentResult(
        agent="back", phase=Phase.STUBS, status="feedback_sent", iteration=3,
    )
    other_agent_result = AgentResult(
        agent="front", phase=Phase.STUBS, status="success", iteration=1,
    )

    async def fake_fetch_run_state(config, run_id):
        return {
            "status": RunStatus.FAILED, "current_phase": "STUBS", "agent_sequence": ["back", "front"],
            "requires_manual_intervention": True,
            "intervention_reason": "Agent 'back' a atteint la limite de 3 itérations.",
            "agent_results": [other_agent_result, failed_result],
            "retry_scope": {"back": {"backend/routers/tasks.py": "erreur obsolète"}},
        }

    monkeypatch.setattr(queries_module, "fetch_run_state", fake_fetch_run_state)

    states = [{
        "status": RunStatus.COMPLETED, "current_phase": "CLOTURE", "agent_sequence": [],
        "card_root_path": f"specs/{_RUN_ID}/card-root.md",
    }]
    graph = _fake_graph(states, [])

    async def fake_build_graph(config):
        return graph

    monkeypatch.setattr(run_flow_module, "build_graph", fake_build_graph)

    bot = _FakeBot()
    result = await run_flow_module.retry_failed_run(bot, _CHAT_ID, _THREAD_ID, config, _FEATURE_NAME)
    assert result == {}
    await _active_runs[_THREAD_ID].task

    assert len(graph.update_calls) == 1
    _, update = graph.update_calls[0]
    assert update["status"] == RunStatus.IN_PROGRESS
    assert update["requires_manual_intervention"] is False
    assert update["intervention_reason"] is None
    # "back"/STUBS purgé (l'agent en échec), "front"/STUBS conservé (agent
    # différent, budget d'itérations indépendant).
    assert update["agent_results"] == [other_agent_result]
    # retry_scope["back"] purgé aussi (voir _reset_failed_state_for_retry) —
    # régénération complète propre plutôt qu'une correction ciblée avec un
    # message d'erreur obsolète.
    assert update["retry_scope"] == {}

    entry = await planification.find_entry(config, _FEATURE_NAME)
    assert entry.statut == "fait"


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


# --- stop_active_run (ADR 0015, Décision 6, /stop) ---

async def test_stop_active_run_returns_none_when_nothing_active():
    assert await stop_active_run(_THREAD_ID) is None


async def test_stop_active_run_cancels_task_and_commits(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace,
):
    calls: dict = {}

    async def fake_commit_safety_snapshot(repo_path, message):
        calls["commit_repo_path"] = repo_path
        return "abc123"

    async def fake_current_branch(repo_path):
        return "studio/ajout-panier"

    async def fake_push_branch(repo_path, branch, remote="origin"):
        calls["push"] = (repo_path, branch)

    monkeypatch.setattr(run_flow_module, "commit_safety_snapshot", fake_commit_safety_snapshot)
    monkeypatch.setattr(run_flow_module, "current_branch", fake_current_branch)
    monkeypatch.setattr(run_flow_module, "push_branch", fake_push_branch)

    task = asyncio.create_task(asyncio.sleep(100))
    _active_runs[_THREAD_ID] = run_flow_module._RunState(
        config=config, run_id=_RUN_ID, feature_name=_FEATURE_NAME,
        chat_id=_CHAT_ID, message_id=1, task=task,
    )

    result = await stop_active_run(_THREAD_ID)
    await asyncio.sleep(0)  # laisse la cancellation se propager

    assert result == {"feature_name": _FEATURE_NAME, "run_id": _RUN_ID, "commit": "abc123"}
    assert task.cancelled()
    assert calls["push"] == (calls["commit_repo_path"], "studio/ajout-panier")
    assert _THREAD_ID not in _active_runs


async def test_stop_active_run_skips_push_when_nothing_to_commit(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace,
):
    async def fake_commit_safety_snapshot(repo_path, message):
        return None

    monkeypatch.setattr(run_flow_module, "commit_safety_snapshot", fake_commit_safety_snapshot)

    _active_runs[_THREAD_ID] = run_flow_module._RunState(
        config=config, run_id=_RUN_ID, feature_name=_FEATURE_NAME,
        chat_id=_CHAT_ID, message_id=1, task=None,
    )

    result = await stop_active_run(_THREAD_ID)

    assert result == {"feature_name": _FEATURE_NAME, "run_id": _RUN_ID, "commit": None}
