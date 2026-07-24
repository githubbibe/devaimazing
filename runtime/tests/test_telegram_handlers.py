"""
Tests de studio.telegram.handlers — logique de dispatch et de confirmation
testée directement (types simples, pas de vrais objets Message/CallbackQuery
aiogram, voir docstring du module testé). queries.py/registry.py (via ses
propres tests) sont mockés ici : ce qu'on vérifie est le routage/dispatch et
le cycle de confirmation, pas l'implémentation des outils eux-mêmes.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import studio.tools.queries as queries_module
import studio.tools.registry as registry_module
import yaml
from studio.telegram.handlers import (
    _pending_confirmations,
    handle_confirmation_callback,
    handle_slash_command,
)

_ALLOWED_CHAT_ID = 42


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {"models": {"pm_opus": "claude-opus-4-8"}})
    _write_yaml(
        config_dir / "projects" / "demo.yml",
        {"repo_path": str(tmp_path / "demo"), "telegram": {"thread_id": 111}},
    )
    return config_dir


async def test_wrong_chat_id_returns_none(config_dir: Path):
    reply = await handle_slash_command(
        "/status run-1",
        chat_id=999,
        message_thread_id=111,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply is None


async def test_non_slash_text_returns_none(config_dir: Path):
    reply = await handle_slash_command(
        "arrête le run",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=111,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply is None


async def test_projects_command_in_general_topic(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_list_projects(config_dir):
        return ["demo"]

    monkeypatch.setattr(queries_module, "list_projects", fake_list_projects)

    reply = await handle_slash_command(
        "/projects",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "demo" in reply.text
    assert reply.confirmation_id is None


async def test_project_scoped_command_in_general_topic_asks_to_use_project_topic(
    config_dir: Path,
):
    reply = await handle_slash_command(
        "/status run-1",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "topic" in reply.text


async def test_project_scoped_command_in_known_topic(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_get_run_snapshot(config, run_id):
        assert config.project_name == "demo"
        return {"found": True, "status": "IN_PROGRESS", "current_phase": "STUBS"}

    monkeypatch.setattr(queries_module, "get_run_snapshot", fake_get_run_snapshot)

    reply = await handle_slash_command(
        "/status run-1",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=111,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "IN_PROGRESS" in reply.text


async def test_unknown_topic_asks_to_use_project_topic(config_dir: Path):
    reply = await handle_slash_command(
        "/status run-1",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=999,  # aucun projet associé à ce thread_id
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "topic" in reply.text


# --- creer_projet (pas de confirmation : le handler s'exécute dès ce
# premier appel à execute_tool, dans handle_slash_command lui-même — pas
# via handle_confirmation_callback, contrairement à archive_projet) ---

async def test_new_command_creates_topic_through_handler(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_create_forum_topic(chat_id, name):
        assert chat_id == _ALLOWED_CHAT_ID
        assert name == "demo"
        return SimpleNamespace(message_thread_id=555)

    captured = {}

    async def fake_set_project_thread_id(path, thread_id):
        captured["thread_id"] = thread_id

    monkeypatch.setattr(registry_module, "set_project_thread_id", fake_set_project_thread_id)
    fake_bot = SimpleNamespace(create_forum_topic=fake_create_forum_topic)

    reply = await handle_slash_command(
        "/new demo",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,  # /new est une commande General
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
        bot=fake_bot,
    )

    assert reply.confirmation_id is None
    assert "demo" in reply.text
    assert captured["thread_id"] == 555


async def test_new_command_without_bot_kwarg_returns_error(config_dir: Path):
    # Régression : creer_projet (requiert_confirmation=False) s'exécute
    # dans handle_slash_command lui-même, pas dans handle_confirmation_callback
    # — sans bot transmis à execute_tool ici, le handler échouait toujours.
    reply = await handle_slash_command(
        "/new demo",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
        # bot non fourni : creer_projet doit répondre par une erreur claire,
        # pas planter — mais ce n'est pas le chemin nominal du bot réel
        # (build_router transmet toujours message.bot).
    )

    assert "Telegram" in reply.text


# --- confirmation (archive_projet) ---

async def test_archive_command_returns_confirmation_id(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    reply = await handle_slash_command(
        "/archive demo",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,  # /archive est une commande General
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply.confirmation_id is not None
    assert reply.confirmation_id in _pending_confirmations


async def test_confirmation_callback_yes_executes_tool(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_commit_safety_snapshot(repo_path, message, tracer=None):
        return None

    async def fake_close_forum_topic(chat_id, message_thread_id):
        return True

    monkeypatch.setattr(registry_module, "commit_safety_snapshot", fake_commit_safety_snapshot)

    reply = await handle_slash_command(
        "/archive demo",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )
    fake_bot = SimpleNamespace(close_forum_topic=fake_close_forum_topic)

    result_text = await handle_confirmation_callback(
        f"confirm:{reply.confirmation_id}:yes",
        chat_id=_ALLOWED_CHAT_ID,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        bot=fake_bot,
    )

    assert "demo" in result_text
    assert reply.confirmation_id not in _pending_confirmations  # consommée


async def test_confirmation_callback_no_cancels(config_dir: Path):
    reply = await handle_slash_command(
        "/archive demo",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    result_text = await handle_confirmation_callback(
        f"confirm:{reply.confirmation_id}:no",
        chat_id=_ALLOWED_CHAT_ID,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        bot=object(),
    )

    assert result_text == "Annulé."
    assert reply.confirmation_id not in _pending_confirmations


async def test_confirmation_callback_unknown_id_returns_expired_message():
    result_text = await handle_confirmation_callback(
        "confirm:inconnu:yes",
        chat_id=_ALLOWED_CHAT_ID,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        bot=object(),
    )

    assert "expirée" in result_text


async def test_confirmation_callback_wrong_chat_id_returns_none():
    result_text = await handle_confirmation_callback(
        "confirm:x:yes",
        chat_id=999,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        bot=object(),
    )

    assert result_text is None


async def test_confirmation_callback_unknown_prefix_returns_none():
    result_text = await handle_confirmation_callback(
        "autre:x:yes",
        chat_id=_ALLOWED_CHAT_ID,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        bot=object(),
    )

    assert result_text is None
