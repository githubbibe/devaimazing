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
import studio.devaimazing.agent as agent_module
import studio.telegram.handlers as handlers_module
import studio.tools.queries as queries_module
import studio.tools.registry as registry_module
import yaml
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from studio.telegram.handlers import (
    _pending_confirmations,
    handle_confirmation_callback,
    handle_natural_language,
    handle_slash_command,
    resolve_message_text,
)
from studio.tools.whisper import ExternalServiceError

_ALLOWED_CHAT_ID = 42


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"pm_opus": "claude-opus-4-8", "devaimazing": "gemma3:4b"},
    })
    _write_yaml(
        config_dir / "projects" / "demo.yml",
        {"repo_path": str(tmp_path / "demo"), "telegram": {"thread_id": 111}},
    )
    return config_dir


def _fake_run_ollama(content: str):
    async def _run_ollama(**_kwargs):
        return {"content": content, "tokens_prompt": 1, "tokens_completion": 1, "duration_ms": 0}

    return _run_ollama


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


async def test_archive_command_without_name_in_project_topic_infers_name(
    config_dir: Path,
):
    # /archive sans argument depuis le topic-projet (ADR 0015, Décision 5) —
    # config_dir associe thread_id=111 au projet "demo" (voir fixture).
    reply = await handle_slash_command(
        "/archive",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=111,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply.confirmation_id is not None
    tool_name, args, _config = _pending_confirmations[reply.confirmation_id]
    assert tool_name == "archive_projet"
    assert args == {"name": "demo"}


async def test_archive_command_without_name_in_unknown_topic_returns_clear_error(
    config_dir: Path,
):
    reply = await handle_slash_command(
        "/archive",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=999,  # aucun projet associé à ce thread_id
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply.confirmation_id is None
    assert "aucun projet" in reply.text


async def test_archive_command_without_name_in_general_requires_explicit_name(
    config_dir: Path,
):
    # Depuis General (pas de topic), le nom reste requis explicitement —
    # execute_tool produit son message d'erreur habituel (argument manquant).
    reply = await handle_slash_command(
        "/archive",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply.confirmation_id is None
    assert "name" in reply.text


async def test_confirmation_callback_yes_executes_tool(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_commit_safety_snapshot(repo_path, message, tracer=None):
        return None

    async def fake_delete_forum_topic(chat_id, message_thread_id):
        return True

    monkeypatch.setattr(registry_module, "commit_safety_snapshot", fake_commit_safety_snapshot)

    reply = await handle_slash_command(
        "/archive demo",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )
    fake_bot = SimpleNamespace(delete_forum_topic=fake_delete_forum_topic)

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


# --- langage naturel (Devaimazing, ADR 0013 tranche S4) ---

async def test_natural_language_wrong_chat_id_returns_none(config_dir: Path):
    reply = await handle_natural_language(
        "bonjour",
        chat_id=999,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply is None


async def test_natural_language_unconfigured_model_returns_none(tmp_path: Path):
    unconfigured_config_dir = tmp_path / "config_no_devaimazing"
    _write_yaml(unconfigured_config_dir / "studio.yml", {"models": {"pm_opus": "x"}})

    reply = await handle_natural_language(
        "bonjour",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=unconfigured_config_dir,
    )

    assert reply is None


async def test_natural_language_no_tool_call_returns_reply(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    monkeypatch.setattr(
        agent_module, "run_ollama", _fake_run_ollama('{"reply": "Bonjour !", "tool_call": null}')
    )

    reply = await handle_natural_language(
        "bonjour",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply.text == "Bonjour !"
    assert reply.confirmation_id is None


async def test_natural_language_general_scope_tool_works_from_general(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_list_projects(config_dir):
        return ["demo"]

    monkeypatch.setattr(queries_module, "list_projects", fake_list_projects)
    monkeypatch.setattr(
        agent_module, "run_ollama",
        _fake_run_ollama('{"reply": "", "tool_call": {"name": "lister_projets", "arguments": {}}}'),
    )

    reply = await handle_natural_language(
        "liste les projets",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,  # General
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "demo" in reply.text


async def test_natural_language_project_scoped_tool_in_general_asks_to_use_topic(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    monkeypatch.setattr(
        agent_module, "run_ollama",
        _fake_run_ollama(
            '{"reply": "", "tool_call": {"name": "lire_statut", "arguments": {"run_id": "r1"}}}'
        ),
    )

    reply = await handle_natural_language(
        "statut du run r1",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,  # General : pas de projet résolvable pour un outil scopé
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "topic" in reply.text


async def test_natural_language_project_scoped_tool_in_known_topic(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_get_run_snapshot(config, run_id):
        assert config.project_name == "demo"
        return {"found": True, "status": "IN_PROGRESS", "current_phase": "STUBS"}

    monkeypatch.setattr(queries_module, "get_run_snapshot", fake_get_run_snapshot)
    monkeypatch.setattr(
        agent_module, "run_ollama",
        _fake_run_ollama(
            '{"reply": "", "tool_call": {"name": "lire_statut", "arguments": {"run_id": "r1"}}}'
        ),
    )

    reply = await handle_natural_language(
        "statut du run r1",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=111,  # topic du projet "demo"
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "IN_PROGRESS" in reply.text


async def test_natural_language_needs_confirmation_registers_pending(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    monkeypatch.setattr(
        agent_module, "run_ollama",
        _fake_run_ollama(
            '{"reply": "", "tool_call": {"name": "archive_projet", "arguments": {"name": "demo"}}}'
        ),
    )

    reply = await handle_natural_language(
        "archive le projet demo",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,  # /archive est General-scope
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert reply.confirmation_id is not None
    assert reply.confirmation_id in _pending_confirmations

    # Consomme la confirmation en attente pour ne pas polluer le dict
    # module-level partagé entre tests (voir _pending_confirmations).
    await handle_confirmation_callback(
        f"confirm:{reply.confirmation_id}:no",
        chat_id=_ALLOWED_CHAT_ID,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        bot=object(),
    )


async def test_natural_language_hallucinated_tool_name_in_general_returns_error_not_topic_prompt(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    """
    Régression : un nom d'outil halluciné (proche mais absent de
    TOOL_REGISTRY, voir docs/roadmap.md) ne doit pas être traité comme un
    outil scopé-projet en General — sans le court-circuit de
    _resolve_config_for_tool, ce cas produisait le message trompeur
    « utilisez le topic d'un projet » plutôt que l'erreur réelle
    (outil inconnu).
    """
    monkeypatch.setattr(
        agent_module, "run_ollama",
        _fake_run_ollama('{"reply": "", "tool_call": {"name": "lire_projets", "arguments": {}}}'),
    )

    reply = await handle_natural_language(
        "fais un truc",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,  # General
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "topic" not in reply.text
    assert "lire_projets" in reply.text or "inconnu" in reply.text.lower()


async def test_on_message_ignores_bot_authored_messages():
    from studio.telegram.handlers import build_router

    router = build_router(config_dir=None, allowed_chat_id=_ALLOWED_CHAT_ID)
    on_message = router.message.handlers[0].callback

    calls = []

    async def fake_reply(text, reply_markup=None):
        calls.append(text)

    message = SimpleNamespace(
        text="bonjour",
        from_user=SimpleNamespace(is_bot=True),
        chat=SimpleNamespace(id=_ALLOWED_CHAT_ID),
        message_thread_id=None,
        bot=None,
        reply=fake_reply,
    )

    await on_message(message)

    assert calls == []


# --- transcription vocale (Whisper, ADR 0014) ---

class _FakeBuffer:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


async def test_resolve_message_text_prefers_typed_text_over_audio(config_dir: Path):
    async def fake_download(file_id):
        raise AssertionError("download ne doit pas être appelé si du texte est déjà présent")

    bot = SimpleNamespace(download=fake_download)

    text = await resolve_message_text(
        text="salut", voice_file_id="voice123", audio_file_id=None,
        bot=bot, config_dir=config_dir,
    )

    assert text == "salut"


async def test_resolve_message_text_transcribes_voice(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    captured = {}

    async def fake_download(file_id):
        captured["file_id"] = file_id
        return _FakeBuffer(b"audio-bytes")

    async def fake_transcribe(audio_bytes, *, base_url, language):
        captured["audio_bytes"] = audio_bytes
        captured["base_url"] = base_url
        captured["language"] = language
        return "quel est le statut du run r1"

    monkeypatch.setattr(handlers_module, "transcribe_voice_message", fake_transcribe)
    bot = SimpleNamespace(download=fake_download)

    text = await resolve_message_text(
        text=None, voice_file_id="voice123", audio_file_id=None,
        bot=bot, config_dir=config_dir,
    )

    assert text == "quel est le statut du run r1"
    assert captured["file_id"] == "voice123"
    assert captured["audio_bytes"] == b"audio-bytes"
    assert captured["language"] == "fr"


async def test_resolve_message_text_no_text_no_audio_returns_none(config_dir: Path):
    text = await resolve_message_text(
        text=None, voice_file_id=None, audio_file_id=None,
        bot=SimpleNamespace(), config_dir=config_dir,
    )

    assert text is None


async def test_resolve_message_text_download_failure_returns_none(config_dir: Path):
    async def fake_download(file_id):
        raise TelegramAPIError(method=None, message="fichier trop gros")

    text = await resolve_message_text(
        text=None, voice_file_id="voice123", audio_file_id=None,
        bot=SimpleNamespace(download=fake_download), config_dir=config_dir,
    )

    assert text is None


async def test_resolve_message_text_transcription_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_download(file_id):
        return _FakeBuffer(b"audio-bytes")

    async def fake_transcribe(audio_bytes, *, base_url, language):
        raise ExternalServiceError("whisper.cpp injoignable")

    monkeypatch.setattr(handlers_module, "transcribe_voice_message", fake_transcribe)

    text = await resolve_message_text(
        text=None, voice_file_id="voice123", audio_file_id=None,
        bot=SimpleNamespace(download=fake_download), config_dir=config_dir,
    )

    assert text is None


async def test_resolve_message_text_empty_transcript_returns_none(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
):
    async def fake_download(file_id):
        return _FakeBuffer(b"silence")

    async def fake_transcribe(audio_bytes, *, base_url, language):
        return ""

    monkeypatch.setattr(handlers_module, "transcribe_voice_message", fake_transcribe)

    text = await resolve_message_text(
        text=None, voice_file_id="voice123", audio_file_id=None,
        bot=SimpleNamespace(download=fake_download), config_dir=config_dir,
    )

    assert text is None


async def test_on_message_voice_with_failed_transcription_sends_error_reply():
    router = handlers_module.build_router(config_dir=None, allowed_chat_id=_ALLOWED_CHAT_ID)
    on_message = router.message.handlers[0].callback

    async def fake_download(file_id):
        raise TelegramAPIError(method=None, message="erreur réseau")

    calls = []

    async def fake_reply(text, reply_markup=None):
        calls.append(text)

    message = SimpleNamespace(
        text=None,
        voice=SimpleNamespace(file_id="voice123"),
        audio=None,
        from_user=SimpleNamespace(is_bot=False),
        chat=SimpleNamespace(id=_ALLOWED_CHAT_ID),
        message_thread_id=None,
        bot=SimpleNamespace(download=fake_download),
        reply=fake_reply,
    )

    await on_message(message)

    assert len(calls) == 1
    assert "transcrire" in calls[0]


# --- /stop, priorité absolue (ADR 0015, Décision 6) ---

async def test_stop_command_bypasses_pending_reply_handlers(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    import studio.telegram.pm_dialogue as pm_dialogue_module
    import studio.telegram.run_flow as run_flow_module

    async def fail_if_called_async(*args, **kwargs):
        raise AssertionError("ne doit pas être appelé : /stop a priorité absolue")

    monkeypatch.setattr(handlers_module, "handle_project_name_reply", fail_if_called_async)
    monkeypatch.setattr(handlers_module, "handle_dialogue_reply", fail_if_called_async)
    monkeypatch.setattr(handlers_module, "handle_run_reply", fail_if_called_async)

    async def fake_stop_active_run(message_thread_id):
        return None

    def fake_cancel_dialogue(message_thread_id):
        return False

    monkeypatch.setattr(run_flow_module, "stop_active_run", fake_stop_active_run)
    monkeypatch.setattr(pm_dialogue_module, "cancel_dialogue", fake_cancel_dialogue)

    router = handlers_module.build_router(config_dir=config_dir, allowed_chat_id=_ALLOWED_CHAT_ID)
    on_message = router.message.handlers[0].callback

    replies = []

    async def fake_reply(text, reply_markup=None):
        replies.append(text)

    message = SimpleNamespace(
        text="/stop",
        from_user=SimpleNamespace(is_bot=False),
        chat=SimpleNamespace(id=_ALLOWED_CHAT_ID),
        message_thread_id=111,  # thread_id du projet "demo" (fixture config_dir)
        bot=None,
        reply=fake_reply,
    )

    await on_message(message)

    assert len(replies) == 1
    assert "aucun traitement en cours" in replies[0]


# --- bouton persistant "Menu →" et menu à boutons (ADR 0015, Décision 7) ---

async def test_menu_button_sends_root_menu(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    calls = {}

    async def fake_send_root_menu(bot, chat_id, message_thread_id, config_dir_arg):
        calls["args"] = (chat_id, message_thread_id, config_dir_arg)

    monkeypatch.setattr(handlers_module.menu, "send_root_menu", fake_send_root_menu)

    router = handlers_module.build_router(config_dir=config_dir, allowed_chat_id=_ALLOWED_CHAT_ID)
    on_message = router.message.handlers[0].callback

    message = SimpleNamespace(
        text=handlers_module.menu.MENU_BUTTON_LABEL,
        from_user=SimpleNamespace(is_bot=False),
        chat=SimpleNamespace(id=_ALLOWED_CHAT_ID),
        message_thread_id=111,
        bot=object(),
        reply=None,
    )

    await on_message(message)

    assert calls["args"] == (_ALLOWED_CHAT_ID, 111, config_dir)


async def test_confirmation_callback_attaches_root_menu_keyboard(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    async def fake_commit_safety_snapshot(repo_path, message, tracer=None):
        return None

    async def fake_delete_forum_topic(chat_id, message_thread_id):
        return True

    monkeypatch.setattr(registry_module, "commit_safety_snapshot", fake_commit_safety_snapshot)

    reply = await handle_slash_command(
        "/archive demo",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=None,
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    router = handlers_module.build_router(config_dir=config_dir, allowed_chat_id=_ALLOWED_CHAT_ID)
    on_callback = router.callback_query.handlers[0].callback

    edits = []

    async def fake_edit_text(text, reply_markup=None):
        edits.append({"text": text, "reply_markup": reply_markup})

    async def fake_answer():
        return None

    fake_bot = SimpleNamespace(delete_forum_topic=fake_delete_forum_topic)
    callback = SimpleNamespace(
        data=f"confirm:{reply.confirmation_id}:yes",
        message=SimpleNamespace(
            chat=SimpleNamespace(id=_ALLOWED_CHAT_ID),
            message_thread_id=None,
            edit_text=fake_edit_text,
        ),
        bot=fake_bot,
        answer=fake_answer,
    )

    await on_callback(callback)

    assert len(edits) == 1
    assert edits[0]["reply_markup"] is not None
    assert "Nouveau projet" in [
        b.text for row in edits[0]["reply_markup"].inline_keyboard for b in row
    ]


# --- réponse au callback avant travail long + robustesse "message not modified" ---

async def test_menu_callback_answers_before_running_long_action(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    order = []

    async def fake_handle_menu_callback(callback_data, *, chat_id, message_thread_id, config_dir, bot):
        order.append("handle_menu_callback")
        return "texte", handlers_module.menu.build_root_keyboard(in_topic=True)

    monkeypatch.setattr(handlers_module.menu, "handle_menu_callback", fake_handle_menu_callback)

    router = handlers_module.build_router(config_dir=config_dir, allowed_chat_id=_ALLOWED_CHAT_ID)
    on_menu_callback = router.callback_query.handlers[1].callback

    async def fake_answer():
        order.append("answer")

    async def fake_edit_text(text, reply_markup=None):
        order.append("edit_text")

    callback = SimpleNamespace(
        data="menu:root",
        message=SimpleNamespace(
            chat=SimpleNamespace(id=_ALLOWED_CHAT_ID),
            message_thread_id=111,
            edit_text=fake_edit_text,
        ),
        bot=None,
        answer=fake_answer,
    )

    await on_menu_callback(callback)

    assert order == ["answer", "handle_menu_callback", "edit_text"]


async def test_menu_callback_ignores_message_not_modified_error(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    async def fake_handle_menu_callback(callback_data, *, chat_id, message_thread_id, config_dir, bot):
        return "texte", handlers_module.menu.build_root_keyboard(in_topic=True)

    monkeypatch.setattr(handlers_module.menu, "handle_menu_callback", fake_handle_menu_callback)

    router = handlers_module.build_router(config_dir=config_dir, allowed_chat_id=_ALLOWED_CHAT_ID)
    on_menu_callback = router.callback_query.handlers[1].callback

    async def fake_answer():
        return None

    async def fake_edit_text(text, reply_markup=None):
        raise TelegramBadRequest(method=None, message="message is not modified: x")

    callback = SimpleNamespace(
        data="menu:root",
        message=SimpleNamespace(
            chat=SimpleNamespace(id=_ALLOWED_CHAT_ID),
            message_thread_id=111,
            edit_text=fake_edit_text,
        ),
        bot=None,
        answer=fake_answer,
    )

    await on_menu_callback(callback)  # ne doit pas lever
