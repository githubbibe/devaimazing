"""
Tests de studio.telegram.new_project_flow (ADR 0015, phase 2 d'implémentation)
— git/gh mockés (comme test_cli.py::test_new_project_*), bot Telegram
remplacé par un objet simple qui enregistre les appels (comme
test_telegram_handlers.py, pas de vrais objets aiogram). start_project_dialogue
mocké : testé séparément dans test_telegram_pm_dialogue.py.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import studio.telegram.new_project_flow as new_project_flow_module
from studio.telegram.new_project_flow import (
    _pending_project_name,
    handle_project_name_reply,
    start_new_project_flow,
)

_CHAT_ID = 42
_THREAD_ID = 999


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {"models": {"pm_opus": "claude-opus-4-8"}})
    return config_dir


@pytest.fixture(autouse=True)
def _projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "aimazing-projects"
    monkeypatch.setattr(new_project_flow_module, "_PROJECTS_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def _clear_pending_state():
    _pending_project_name.clear()
    yield
    _pending_project_name.clear()


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch: pytest.MonkeyPatch):
    """Par défaut, gh indisponible (chemin le plus simple) — les tests qui
    veulent le chemin gh-disponible le remontent explicitement."""
    monkeypatch.setattr(new_project_flow_module.shutil, "which", lambda name: None)


@pytest.fixture(autouse=True)
def _fake_git(monkeypatch: pytest.MonkeyPatch):
    calls: list = []

    async def fake_init_repo(repo_path, initial_branch="develop"):
        calls.append(("init_repo", repo_path, initial_branch))

    async def fake_create_initial_commit(repo_path, name):
        calls.append(("create_initial_commit", repo_path, name))

    monkeypatch.setattr(new_project_flow_module, "init_repo", fake_init_repo)
    monkeypatch.setattr(
        new_project_flow_module, "create_initial_commit", fake_create_initial_commit,
    )
    return calls


@pytest.fixture(autouse=True)
def _mock_project_dialogue(monkeypatch: pytest.MonkeyPatch):
    calls: list = []

    async def fake_start_project_dialogue(bot, chat_id, message_thread_id, config, project_name):
        calls.append((bot, chat_id, message_thread_id, config, project_name))

    monkeypatch.setattr(
        new_project_flow_module, "start_project_dialogue", fake_start_project_dialogue,
    )
    return calls


class _FakeBot:
    def __init__(self, thread_id: int = _THREAD_ID):
        self.messages: list[dict] = []
        self._thread_id = thread_id

    async def send_message(self, chat_id, text, *, message_thread_id=None, reply_markup=None):
        self.messages.append({
            "chat_id": chat_id, "text": text, "message_thread_id": message_thread_id,
        })

    async def create_forum_topic(self, chat_id, name):
        return SimpleNamespace(message_thread_id=self._thread_id)


async def test_start_new_project_flow_asks_for_name(config_dir: Path):
    bot = _FakeBot()

    await start_new_project_flow(bot, _CHAT_ID, config_dir)

    assert len(bot.messages) == 1
    assert bot.messages[0]["message_thread_id"] is None
    assert "nom" in bot.messages[0]["text"]
    assert _pending_project_name[_CHAT_ID] == config_dir


async def test_handle_project_name_reply_without_pending_returns_false(config_dir: Path):
    bot = _FakeBot()

    consumed = await handle_project_name_reply(bot, _CHAT_ID, None, "mon-projet")

    assert consumed is False


async def test_handle_project_name_reply_from_topic_returns_false(config_dir: Path):
    _pending_project_name[_CHAT_ID] = config_dir
    bot = _FakeBot()

    consumed = await handle_project_name_reply(bot, _CHAT_ID, 111, "mon-projet")

    assert consumed is False
    assert _CHAT_ID in _pending_project_name  # pas consommé


async def test_full_flow_creates_project_and_starts_dialogue(
    config_dir: Path, _projects_root: Path, _fake_git: list, _mock_project_dialogue: list,
):
    _pending_project_name[_CHAT_ID] = config_dir
    bot = _FakeBot()

    consumed = await handle_project_name_reply(bot, _CHAT_ID, None, "mon-projet")

    assert consumed is True
    assert _CHAT_ID not in _pending_project_name

    config_path = config_dir / "projects" / "mon-projet.yml"
    assert config_path.is_file()
    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written["repo_path"] == str(_projects_root / "mon-projet")

    assert _fake_git[0] == ("init_repo", _projects_root / "mon-projet", "develop")
    assert _fake_git[1] == ("create_initial_commit", _projects_root / "mon-projet", "mon-projet")

    texts = [m["text"] for m in bot.messages]
    assert any("en cours de création" in t for t in texts)
    assert any("dossier" in t and "créé" in t for t in texts)
    assert any("repo local" in t and "créé" in t for t in texts)
    assert any("gh introuvable" in t for t in texts)
    assert any("Project Manager attend" in t for t in texts)
    # Tous les messages de progression (sauf la demande de nom, pas ici)
    # vont dans le topic nouvellement créé.
    assert all(m["message_thread_id"] == _THREAD_ID for m in bot.messages)

    assert len(_mock_project_dialogue) == 1
    dialogue_bot, dialogue_chat_id, dialogue_thread_id, dialogue_config, dialogue_name = (
        _mock_project_dialogue[0]
    )
    assert dialogue_chat_id == _CHAT_ID
    assert dialogue_thread_id == _THREAD_ID
    assert dialogue_name == "mon-projet"
    assert dialogue_config.project_name == "mon-projet"


async def test_full_flow_with_gh_available_creates_remote(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, _projects_root: Path,
):
    monkeypatch.setattr(new_project_flow_module.shutil, "which", lambda name: "/usr/bin/gh")

    calls: list = []

    async def fake_create_github_remote(repo_path, name, private=True):
        calls.append(("create_github_remote", repo_path, name, private))

    async def fake_push_branch(repo_path, branch, remote="origin"):
        calls.append(("push_branch", repo_path, branch))

    monkeypatch.setattr(new_project_flow_module, "create_github_remote", fake_create_github_remote)
    monkeypatch.setattr(new_project_flow_module, "push_branch", fake_push_branch)

    _pending_project_name[_CHAT_ID] = config_dir
    bot = _FakeBot()

    await handle_project_name_reply(bot, _CHAT_ID, None, "mon-projet")

    assert calls[0] == ("create_github_remote", _projects_root / "mon-projet", "mon-projet", True)
    assert calls[1] == ("push_branch", _projects_root / "mon-projet", "develop")
    texts = [m["text"] for m in bot.messages]
    assert any("repo distant" in t and "créé" in t for t in texts)
    assert not any("gh introuvable" in t for t in texts)


async def test_handle_project_name_reply_config_already_exists(config_dir: Path):
    config_path = config_dir / "projects" / "mon-projet.yml"
    _write_yaml(config_path, {"name": "mon-projet"})
    _pending_project_name[_CHAT_ID] = config_dir
    bot = _FakeBot()

    consumed = await handle_project_name_reply(bot, _CHAT_ID, None, "mon-projet")

    assert consumed is True
    assert len(bot.messages) == 1
    assert "existe déjà" in bot.messages[0]["text"]
