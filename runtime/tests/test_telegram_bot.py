"""
Tests de studio.telegram.bot.build_bot_and_dispatcher — construction locale
uniquement (aiogram.Bot() ne fait pas d'appel réseau à la construction),
aucun test de run_bot lui-même (bloquerait sur un vrai polling réseau, hors
scope des tests automatisés — voir docs/roadmap.md).
"""

from pathlib import Path

import pytest
import yaml
from aiogram import Bot, Dispatcher
from studio.telegram.bot import build_bot_and_dispatcher


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_build_bot_and_dispatcher_missing_token_raises(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "telegram": {"token": "<PLACEHOLDER_TELEGRAM_TOKEN>", "allowed_chat_id": 42},
    })

    with pytest.raises(ValueError, match="token"):
        build_bot_and_dispatcher(config_dir)


def test_build_bot_and_dispatcher_missing_chat_id_raises(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "telegram": {"token": "123:abc", "allowed_chat_id": "<PLACEHOLDER_CHAT_ID>"},
    })

    with pytest.raises(ValueError, match="allowed_chat_id"):
        build_bot_and_dispatcher(config_dir)


def test_build_bot_and_dispatcher_valid_config(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "telegram": {"token": "123456:ABCdefGhIJKlmNoPQRsTUVwxyZ1234567890", "allowed_chat_id": 42},
    })

    bot, dispatcher = build_bot_and_dispatcher(config_dir)

    assert isinstance(bot, Bot)
    assert isinstance(dispatcher, Dispatcher)


