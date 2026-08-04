"""
Tests de studio.telegram.menu (arborescence de boutons, ADR 0015, Décision 7)
— les flux délégués (new_project_flow, pm_dialogue, run_flow) sont mockés
(comme test_registry.py pour new_feature/new_project/run_feature) : ce qui
est vérifié ici est la construction des écrans et le routage des
callback_data, pas l'implémentation des flux eux-mêmes.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import studio.telegram.confirmations as confirmations_module
import studio.telegram.menu as menu_module
import studio.telegram.new_project_flow as new_project_flow_module
import studio.telegram.pm_dialogue as pm_dialogue_module
import studio.telegram.run_flow as run_flow_module
from studio.tools import planification
from studio.tools.planification import PlanificationEntry

_CHAT_ID = 42


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {"models": {"pm_opus": "claude-opus-4-8"}})
    _write_yaml(config_dir / "projects" / "demo.yml", {
        "repo_path": str(tmp_path / "demo-repo"), "telegram": {"thread_id": 111},
    })
    _write_yaml(config_dir / "projects" / "sans-topic.yml", {
        "repo_path": str(tmp_path / "sans-topic-repo"),
    })
    return config_dir


@pytest.fixture(autouse=True)
def _clear_pending_confirmations():
    confirmations_module.pending_confirmations.clear()
    yield
    confirmations_module.pending_confirmations.clear()


class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, *, message_thread_id=None, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "message_thread_id": message_thread_id})


def _button_labels(keyboard) -> list[str]:
    return [button.text for row in keyboard.inline_keyboard for button in row]


def _callback_data(keyboard) -> list[str]:
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


# --- build_root_keyboard ---

def test_build_root_keyboard_general_has_new_project():
    keyboard = menu_module.build_root_keyboard(in_topic=False)
    assert "Nouveau projet" in _button_labels(keyboard)
    assert len(keyboard.inline_keyboard) == 4


def test_build_root_keyboard_topic_has_no_new_project():
    keyboard = menu_module.build_root_keyboard(in_topic=True)
    assert "Nouveau projet" not in _button_labels(keyboard)
    assert len(keyboard.inline_keyboard) == 3


# --- menu:root ---

async def test_root_screen_from_general(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:root", chat_id=_CHAT_ID, message_thread_id=None, config_dir=config_dir, bot=_FakeBot(),
    )
    assert "Nouveau projet" in _button_labels(keyboard)
    assert "demo" not in text


async def test_root_screen_from_known_topic(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:root", chat_id=_CHAT_ID, message_thread_id=111, config_dir=config_dir, bot=_FakeBot(),
    )
    assert "demo" in text
    assert "Nouveau projet" not in _button_labels(keyboard)


# --- menu:feature_menu (sous-menu Créer/Modifier/Lancer) ---

async def test_root_screen_has_single_feature_button(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:root", chat_id=_CHAT_ID, message_thread_id=111, config_dir=config_dir, bot=_FakeBot(),
    )
    labels = _button_labels(keyboard)
    assert "Feature..." in labels
    assert "Nouvelle feature" not in labels
    assert "Modifier une feature" not in labels
    assert "Lancer une feature" not in labels


async def test_feature_menu_shows_submenu(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:feature_menu", chat_id=_CHAT_ID, message_thread_id=111,
        config_dir=config_dir, bot=_FakeBot(),
    )

    labels = _button_labels(keyboard)
    assert labels == ["Créer", "Modifier", "Lancer", "◀ Retour"]
    callback_data = _callback_data(keyboard)
    assert callback_data == [
        "menu:new_feature", "menu:modifier_feature", "menu:run_feature", "menu:root",
    ]


# --- menu:new_project ---

async def test_new_project_delegates_to_start_new_project_flow(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    calls = {}

    async def fake_start_new_project_flow(bot, chat_id, config_dir_arg):
        calls["args"] = (bot, chat_id, config_dir_arg)

    monkeypatch.setattr(
        new_project_flow_module, "start_new_project_flow", fake_start_new_project_flow,
    )

    bot = _FakeBot()
    text, _keyboard = await menu_module.handle_menu_callback(
        "menu:new_project", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=bot,
    )

    assert calls["args"] == (bot, _CHAT_ID, config_dir)
    assert "nouveau projet" in text.lower()


# --- menu:new_feature ---

async def test_new_feature_from_topic_delegates_directly(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    calls = {}

    async def fake_start_feature_dialogue(bot, chat_id, message_thread_id, config):
        calls["args"] = (bot, chat_id, message_thread_id, config.project_name)

    monkeypatch.setattr(
        pm_dialogue_module, "start_feature_dialogue", fake_start_feature_dialogue,
    )

    bot = _FakeBot()
    await menu_module.handle_menu_callback(
        "menu:new_feature", chat_id=_CHAT_ID, message_thread_id=111,
        config_dir=config_dir, bot=bot,
    )

    assert calls["args"] == (bot, _CHAT_ID, 111, "demo")


async def test_new_feature_from_general_shows_project_selection(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:new_feature", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "quel projet" in text.lower()
    assert "demo" in _button_labels(keyboard)
    assert f"{menu_module.CALLBACK_PREFIX}:project:new_feature:demo" in _callback_data(keyboard)


async def test_new_feature_project_selection_leaf_delegates(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    calls = {}

    async def fake_start_feature_dialogue(bot, chat_id, message_thread_id, config):
        calls["args"] = (chat_id, message_thread_id, config.project_name)

    monkeypatch.setattr(
        pm_dialogue_module, "start_feature_dialogue", fake_start_feature_dialogue,
    )

    await menu_module.handle_menu_callback(
        "menu:project:new_feature:demo", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert calls["args"] == (_CHAT_ID, 111, "demo")


# --- menu:cadrer_projet ---

async def test_cadrer_projet_from_topic_delegates_directly(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    calls = {}

    async def fake_start_project_dialogue(bot, chat_id, message_thread_id, config, project_name):
        calls["args"] = (bot, chat_id, message_thread_id, project_name)

    monkeypatch.setattr(
        pm_dialogue_module, "start_project_dialogue", fake_start_project_dialogue,
    )

    bot = _FakeBot()
    await menu_module.handle_menu_callback(
        "menu:cadrer_projet", chat_id=_CHAT_ID, message_thread_id=111,
        config_dir=config_dir, bot=bot,
    )

    assert calls["args"] == (bot, _CHAT_ID, 111, "demo")


async def test_cadrer_projet_from_general_shows_project_selection(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:cadrer_projet", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "quel projet" in text.lower()
    assert f"{menu_module.CALLBACK_PREFIX}:project:cadrer_projet:demo" in _callback_data(keyboard)


async def test_cadrer_projet_project_selection_leaf_delegates(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    calls = {}

    async def fake_start_project_dialogue(bot, chat_id, message_thread_id, config, project_name):
        calls["args"] = (chat_id, message_thread_id, project_name)

    monkeypatch.setattr(
        pm_dialogue_module, "start_project_dialogue", fake_start_project_dialogue,
    )

    await menu_module.handle_menu_callback(
        "menu:project:cadrer_projet:demo", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert calls["args"] == (_CHAT_ID, 111, "demo")


# --- menu:archive ---

async def test_archive_from_topic_needs_confirmation(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:archive", chat_id=_CHAT_ID, message_thread_id=111,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "Confirmer" in text
    assert len(confirmations_module.pending_confirmations) == 1
    confirmation_id = next(iter(confirmations_module.pending_confirmations))
    tool_name, args, _config = confirmations_module.pending_confirmations[confirmation_id]
    assert tool_name == "archive_projet"
    assert args == {"name": "demo"}
    assert keyboard.inline_keyboard  # clavier Oui/Non attaché


async def test_archive_via_project_selection_needs_confirmation(config_dir: Path):
    text, _keyboard = await menu_module.handle_menu_callback(
        "menu:project:archive:demo", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "Confirmer" in text
    assert len(confirmations_module.pending_confirmations) == 1


# --- menu:run_feature (liste de features) ---

async def test_run_feature_from_topic_lists_features(config_dir: Path, tmp_path: Path):
    from studio.config import StudioConfig

    project_config = StudioConfig(project_name="demo", config_dir=config_dir)
    await planification.upsert_entry(project_config, PlanificationEntry(
        feature_name="ajout-panier", statut="à faire", run_id="run-1", content_hash="h1",
    ))
    await planification.upsert_entry(project_config, PlanificationEntry(
        feature_name="ajout-recherche", statut="fait", run_id="run-2", content_hash="h2",
    ))

    text, keyboard = await menu_module.handle_menu_callback(
        "menu:run_feature", chat_id=_CHAT_ID, message_thread_id=111,
        config_dir=config_dir, bot=_FakeBot(),
    )

    labels = _button_labels(keyboard)
    assert any("ajout-panier" in label and "à faire" in label for label in labels)
    assert any("ajout-recherche" in label and "fait" in label for label in labels)
    assert "◀ Retour" in labels


async def test_run_feature_without_planification_shows_empty_message(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:run_feature", chat_id=_CHAT_ID, message_thread_id=111,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "Aucune feature" in text
    assert _button_labels(keyboard) == ["◀ Retour"]


async def test_feature_leaf_delegates_to_run_flow_start_run(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    calls = {}

    async def fake_start_run(bot, chat_id, message_thread_id, config, feature_name):
        calls["args"] = (chat_id, message_thread_id, config.project_name, feature_name)
        return {}

    monkeypatch.setattr(run_flow_module, "start_run", fake_start_run)

    await menu_module.handle_menu_callback(
        "menu:feature:demo:ajout-panier", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert calls["args"] == (_CHAT_ID, 111, "demo", "ajout-panier")


# --- menu:modifier_feature (liste de features, ADR 0015 Décision 7 révisée) ---

async def test_modifier_feature_from_topic_lists_features(config_dir: Path, tmp_path: Path):
    from studio.config import StudioConfig

    project_config = StudioConfig(project_name="demo", config_dir=config_dir)
    await planification.upsert_entry(project_config, PlanificationEntry(
        feature_name="ajout-panier", statut="fait", run_id="run-1", content_hash="h1",
    ))

    text, keyboard = await menu_module.handle_menu_callback(
        "menu:modifier_feature", chat_id=_CHAT_ID, message_thread_id=111,
        config_dir=config_dir, bot=_FakeBot(),
    )

    labels = _button_labels(keyboard)
    assert any("ajout-panier" in label and "fait" in label for label in labels)
    assert "◀ Retour" in labels
    callback_data = _callback_data(keyboard)
    assert any(data.startswith("menu:feature_edit:") for data in callback_data if data)


async def test_modifier_feature_without_planification_shows_empty_message(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:modifier_feature", chat_id=_CHAT_ID, message_thread_id=111,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "Aucune feature" in text
    assert _button_labels(keyboard) == ["◀ Retour"]


async def test_feature_edit_leaf_delegates_to_modifier_feature_tool(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path,
):
    calls = {}

    async def fake_start_feature_edit_dialogue(
        bot, chat_id, message_thread_id, config, feature_name, existing_content,
    ):
        calls["args"] = (chat_id, message_thread_id, config.project_name, feature_name)

    monkeypatch.setattr(
        pm_dialogue_module, "start_feature_edit_dialogue", fake_start_feature_edit_dialogue,
    )

    from studio.config import StudioConfig
    project_config = StudioConfig(project_name="demo", config_dir=config_dir)
    await planification.upsert_entry(project_config, PlanificationEntry(
        feature_name="ajout-panier", statut="fait", run_id="run-1", content_hash="h1",
    ))
    card_root = (project_config.repo_path / "specs" / "run-1" / "card-root.md")
    card_root.parent.mkdir(parents=True)
    card_root.write_text("# Fiche ajout-panier\n", encoding="utf-8")

    await menu_module.handle_menu_callback(
        "menu:feature_edit:demo:ajout-panier", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert calls["args"] == (_CHAT_ID, 111, "demo", "ajout-panier")


# --- résolution défensive ---

async def test_unknown_project_returns_error_screen_not_exception(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:project:new_feature:inconnu", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "introuvable" in text.lower() or "sans topic" in text.lower()
    assert "◀ Retour" in _button_labels(keyboard)


async def test_new_feature_project_without_topic_returns_error_screen(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:project:new_feature:sans-topic", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "sans topic" in text.lower()
    assert "◀ Retour" in _button_labels(keyboard)


async def test_unknown_action_returns_error_screen(config_dir: Path):
    text, keyboard = await menu_module.handle_menu_callback(
        "menu:bogus", chat_id=_CHAT_ID, message_thread_id=None,
        config_dir=config_dir, bot=_FakeBot(),
    )

    assert "inconnue" in text.lower()
    assert "◀ Retour" in _button_labels(keyboard)
