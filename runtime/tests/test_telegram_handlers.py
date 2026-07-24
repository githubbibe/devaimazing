"""
Tests de studio.telegram.handlers.handle_slash_command — logique de
dispatch testée directement (types simples, pas de vrais objets Message
aiogram, voir docstring du module testé). queries.py est mocké (comme dans
test_registry.py) : ce qu'on vérifie ici est le routage/dispatch, pas
l'implémentation des outils eux-mêmes.
"""

from pathlib import Path

import pytest
import studio.tools.queries as queries_module
import yaml
from studio.telegram.handlers import handle_slash_command

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

    assert "demo" in reply


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

    assert "topic" in reply


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

    assert "IN_PROGRESS" in reply


async def test_unknown_topic_asks_to_use_project_topic(config_dir: Path):
    reply = await handle_slash_command(
        "/status run-1",
        chat_id=_ALLOWED_CHAT_ID,
        message_thread_id=999,  # aucun projet associé à ce thread_id
        allowed_chat_id=_ALLOWED_CHAT_ID,
        config_dir=config_dir,
    )

    assert "topic" in reply
