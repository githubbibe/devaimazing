"""
Tests de studio.telegram.pm_dialogue (ADR 0015, phase 1 d'implémentation) —
Claude Code CLI mocké (comme test_pm_node.py), bot Telegram remplacé par un
objet simple qui enregistre les appels (comme test_telegram_handlers.py, pas
de vrais objets aiogram).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import studio.nodes.pm as pm_node
import studio.telegram.confirmations as confirmations_module
import studio.telegram.pm_dialogue as pm_dialogue
from studio.config import StudioConfig
from studio.tools.registry import execute_tool
from studio.telegram.handlers import handle_confirmation_callback

_CHAT_ID = 42
_THREAD_ID = 111


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    return repo


@pytest.fixture
def config(tmp_path: Path, repo: Path) -> StudioConfig:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "models": {"pm_opus": "claude-opus-4-8"},
        "claude_code": {"timeout_seconds": 300, "output_format": "json"},
        "structure": {"specs_dir": "specs/"},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(repo)})
    return StudioConfig(project_name="demo", config_dir=config_dir)


@pytest.fixture(autouse=True)
def _clear_pending_state():
    pm_dialogue._pending_dialogues.clear()
    confirmations_module.pending_confirmations.clear()
    yield
    pm_dialogue._pending_dialogues.clear()
    confirmations_module.pending_confirmations.clear()


class _FakeBot:
    def __init__(self):
        self.messages: list[dict] = []
        self.chat_actions: list[dict] = []

    async def send_message(self, chat_id, text, *, message_thread_id=None, reply_markup=None):
        self.messages.append({
            "chat_id": chat_id, "text": text,
            "message_thread_id": message_thread_id, "reply_markup": reply_markup,
        })

    async def send_chat_action(self, chat_id, action, *, message_thread_id=None):
        self.chat_actions.append({
            "chat_id": chat_id, "action": action, "message_thread_id": message_thread_id,
        })


def _fake_claude_result(content: str) -> dict:
    return {
        "content": content,
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "duration_ms": 500,
    }


VALID_FICHE = (
    "**Nom de la feature** : ajout-panier\n"
    "**Objectif brut** : ajouter un panier\n"
)


async def test_start_feature_dialogue_posts_first_question(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    async def fake_run_claude_code(**kwargs):
        return _fake_claude_result("QUESTION: quel est le nom de la feature ?")

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()

    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    assert len(bot.messages) == 1
    assert bot.messages[0]["message_thread_id"] == _THREAD_ID
    assert "nom de la feature" in bot.messages[0]["text"]
    assert bot.messages[0]["reply_markup"] is None
    assert _THREAD_ID in pm_dialogue._pending_dialogues


async def test_handle_dialogue_reply_without_pending_dialogue_returns_false(
    config: StudioConfig,
):
    bot = _FakeBot()

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "peu importe")

    assert consumed is False
    assert bot.messages == []


async def test_handle_dialogue_reply_none_thread_id_returns_false():
    bot = _FakeBot()

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, None, "peu importe")

    assert consumed is False


async def test_handle_dialogue_reply_advances_to_next_question(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result("QUESTION: à quoi ça sert ?"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier")

    assert consumed is True
    assert len(bot.chat_actions) == 1
    assert bot.chat_actions[0]["action"] == "typing"
    assert len(bot.messages) == 2
    assert "à quoi ça sert" in bot.messages[1]["text"]
    assert _THREAD_ID in pm_dialogue._pending_dialogues


async def test_handle_dialogue_reply_validated_draft_presents_confirmation(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig,
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)

    consumed = await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier")

    assert consumed is True
    # Dialogue terminé : plus en attente d'une réponse texte, relayé à la
    # confirmation Oui/Non (même mécanisme que les outils du registre).
    assert _THREAD_ID not in pm_dialogue._pending_dialogues
    assert len(bot.messages) == 2
    draft_message = bot.messages[1]
    assert VALID_FICHE.strip() in draft_message["text"]
    assert draft_message["reply_markup"] is not None
    assert len(confirmations_module.pending_confirmations) == 1


async def test_confirming_draft_writes_card_root(
    monkeypatch: pytest.MonkeyPatch, config: StudioConfig, repo: Path,
):
    responses = [
        _fake_claude_result("QUESTION: quel est le nom de la feature ?"),
        _fake_claude_result(f"FICHE_VALIDEE:\n{VALID_FICHE}"),
    ]

    async def fake_run_claude_code(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pm_node, "run_claude_code", fake_run_claude_code)
    bot = _FakeBot()
    await pm_dialogue.start_feature_dialogue(bot, _CHAT_ID, _THREAD_ID, config)
    await pm_dialogue.handle_dialogue_reply(bot, _CHAT_ID, _THREAD_ID, "ajout-panier")

    confirmation_id = next(iter(confirmations_module.pending_confirmations))
    reply_text = await handle_confirmation_callback(
        f"confirm:{confirmation_id}:yes", chat_id=_CHAT_ID, allowed_chat_id=_CHAT_ID, bot=bot,
    )

    assert reply_text is not None
    # Le run_id n'est plus dans _pending_dialogues (nettoyé) — on le retrouve
    # via le seul dossier créé sous specs/.
    run_dirs = list((repo / "specs").iterdir())
    assert len(run_dirs) == 1
    card_path = run_dirs[0] / "card-root.md"
    assert card_path.read_text(encoding="utf-8") == VALID_FICHE.strip()
