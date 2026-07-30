"""
Arborescence de boutons Telegram (ADR 0015, Décision 7) — navigation par
callback_query, un seul message édité en place à chaque niveau (jamais un
nouveau message par étape), sur le modèle du clavier Oui/Non déjà existant
(studio.telegram.confirmations).

Contrairement aux dialogues de cadrage (pm_dialogue._pending_dialogues) ou
aux confirmations (confirmations.pending_confirmations), la navigation ici
ne maintient AUCUN état en mémoire process : chaque écran est entièrement
reconstruit à partir du seul callback_data (plus message_thread_id du
message et config_dir) — pas d'état à perdre si le bot redémarre pendant
qu'un menu est affiché.

Un seul niveau de retour (« ◀ Retour » ramène toujours à menu:root, jamais
un retour pas-à-pas) : l'arbre ne fait que 2 niveaux (root -> sélection
projet -> feuille, ou root -> liste de features), un vrai back-stack serait
disproportionné pour cette profondeur.
"""

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from studio.config import StudioConfig
from studio.telegram.confirmations import build_confirmation_keyboard, pending_confirmations
from studio.telegram.topics import load_topic_map, resolve_project
from studio.tools import planification
from studio.tools.registry import execute_tool, format_tool_result

CALLBACK_PREFIX = "menu"

_ROOT_ACTIONS_GENERAL = ["new_project", "new_feature", "run_feature", "archive"]
_ROOT_ACTIONS_TOPIC = ["new_feature", "run_feature", "archive"]
_LABELS = {
    "new_project": "Nouveau projet",
    "new_feature": "Nouvelle feature",
    "run_feature": "Lancer une feature",
    "archive": "Archiver ce projet",
}
_BACK_BUTTON = InlineKeyboardButton(text="◀ Retour", callback_data=f"{CALLBACK_PREFIX}:root")

_CONFIG_RESOLUTION_ERRORS = (FileNotFoundError, ValueError)


def _button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def build_root_keyboard(*, in_topic: bool) -> InlineKeyboardMarkup:
    """Un bouton par ligne — actions du topic-projet (3), ou de General (4,
    avec « Nouveau projet » en plus, qui n'a pas de sens dans un topic déjà
    lié à un projet, voir ADR 0015, Décision 7)."""
    actions = _ROOT_ACTIONS_TOPIC if in_topic else _ROOT_ACTIONS_GENERAL
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(_LABELS[action], f"{CALLBACK_PREFIX}:{action}")] for action in actions
    ])


def _project_config(config_dir: Path, project_name: str) -> StudioConfig:
    return StudioConfig(project_name=project_name, config_dir=config_dir)


def _project_thread_id(config_dir: Path, project_name: str) -> Optional[int]:
    config = _project_config(config_dir, project_name)
    thread_id = config.get("telegram", {}).get("thread_id")
    return int(thread_id) if thread_id is not None else None


def _safe_project_thread_id(config_dir: Path, project_name: str) -> Optional[int]:
    """Comme _project_thread_id, mais absorbe un projet introuvable (ex.
    supprimé entre l'affichage d'un écran et le clic) — None dans ce cas,
    traité par les fonctions appelantes comme « pas de topic associé »."""
    try:
        return _project_thread_id(config_dir, project_name)
    except _CONFIG_RESOLUTION_ERRORS:
        return None


def _error_screen(text: str) -> tuple[str, InlineKeyboardMarkup]:
    return text, InlineKeyboardMarkup(inline_keyboard=[[_BACK_BUTTON]])


def _root_screen(config_dir: Path, message_thread_id: Optional[int]) -> tuple[str, InlineKeyboardMarkup]:
    if message_thread_id is None:
        return "Que veux-tu faire ?", build_root_keyboard(in_topic=False)

    project_name = resolve_project(message_thread_id, load_topic_map(config_dir))
    text = f"Projet {project_name} — que veux-tu faire ?" if project_name else "Que veux-tu faire ?"
    return text, build_root_keyboard(in_topic=True)


def _project_selection_screen(config_dir: Path, action: str) -> tuple[str, InlineKeyboardMarkup]:
    projects = sorted(set(load_topic_map(config_dir).values()))
    if not projects:
        return _error_screen("Aucun projet avec un topic Telegram connu.")

    buttons = [
        [_button(name, f"{CALLBACK_PREFIX}:project:{action}:{name}")] for name in projects
    ]
    buttons.append([_BACK_BUTTON])
    return f"{_LABELS[action]} — pour quel projet ?", InlineKeyboardMarkup(inline_keyboard=buttons)


async def _feature_list_screen(
    config_dir: Path, project_name: str,
) -> tuple[str, InlineKeyboardMarkup]:
    try:
        config = _project_config(config_dir, project_name)
    except _CONFIG_RESOLUTION_ERRORS:
        return _error_screen(f"Projet {project_name!r} introuvable.")

    entries = await planification.list_entries(config)
    if not entries:
        return _error_screen(
            f"Aucune feature cadrée pour {project_name!r} — utilise Nouvelle feature d'abord."
        )

    buttons = [
        [_button(
            f"{entry.feature_name} ({entry.statut})",
            f"{CALLBACK_PREFIX}:feature:{project_name}:{entry.feature_name}",
        )]
        for entry in entries
    ]
    buttons.append([_BACK_BUTTON])
    return f"{project_name} — quelle feature lancer ?", InlineKeyboardMarkup(inline_keyboard=buttons)


async def _execute_or_confirm(
    tool_name: str, args: dict[str, Any], config: Any, *, bot: Any, chat_id: int,
    message_thread_id: Optional[int] = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Exécute un outil du registre, ou enregistre une confirmation en
    attente s'il en réclame une (même motif que
    handlers.handle_slash_command/pm_dialogue._present_draft, dupliqué ici
    plutôt que factorisé — pas de module partagé à modifier pour ce
    chantier)."""
    result = await execute_tool(
        tool_name, args, config=config, bot=bot, chat_id=chat_id,
        message_thread_id=message_thread_id,
    )
    if result.status == "needs_confirmation":
        confirmation_id = uuid.uuid4().hex[:12]
        pending_confirmations[confirmation_id] = (tool_name, args, config)
        return result.summary, build_confirmation_keyboard(confirmation_id)

    return format_tool_result(result), InlineKeyboardMarkup(inline_keyboard=[[_BACK_BUTTON]])


async def _start_new_feature(
    config_dir: Path, project_name: str, thread_id: Optional[int], *, bot: Any, chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    if thread_id is None:
        return _error_screen(f"Projet {project_name!r} sans topic Telegram associé.")
    try:
        config = _project_config(config_dir, project_name)
    except _CONFIG_RESOLUTION_ERRORS:
        return _error_screen(f"Projet {project_name!r} introuvable.")

    return await _execute_or_confirm(
        "new_feature", {}, config, bot=bot, chat_id=chat_id, message_thread_id=thread_id,
    )


async def _start_archive(
    config_dir: Path, project_name: str, *, bot: Any, chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    return await _execute_or_confirm(
        "archive_projet", {"name": project_name},
        SimpleNamespace(config_dir=config_dir), bot=bot, chat_id=chat_id,
    )


async def _start_run_feature(
    config_dir: Path, project_name: str, feature_name: str, thread_id: Optional[int], *,
    bot: Any, chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    if thread_id is None:
        return _error_screen(f"Projet {project_name!r} sans topic Telegram associé.")
    try:
        config = _project_config(config_dir, project_name)
    except _CONFIG_RESOLUTION_ERRORS:
        return _error_screen(f"Projet {project_name!r} introuvable.")

    return await _execute_or_confirm(
        "run_feature", {"feature_name": feature_name}, config,
        bot=bot, chat_id=chat_id, message_thread_id=thread_id,
    )


async def send_root_menu(
    bot: Any, chat_id: int, message_thread_id: Optional[int], config_dir: Path,
) -> None:
    """Poste un NOUVEAU message avec le clavier racine — utilisé par la
    commande /menu (voir telegram.handlers._on_message)."""
    text, keyboard = _root_screen(config_dir, message_thread_id)
    await bot.send_message(
        chat_id, text, message_thread_id=message_thread_id, reply_markup=keyboard,
    )


async def handle_menu_callback(
    callback_data: str, *, chat_id: int, message_thread_id: Optional[int],
    config_dir: Path, bot: Any,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Point d'entrée du callback_query `menu:*` (voir telegram.handlers.
    build_router) — toujours un (texte, clavier) à utiliser pour éditer le
    message en place, jamais None (déjà filtré par le préfixe aiogram côté
    appelant, à la différence de handle_confirmation_callback qui partage
    une route générique avec d'autres préfixes).

    Args:
        callback_data: Donnée du callback ("menu:root",
            "menu:project:<action>:<nom>", "menu:feature:<projet>:<feature>", ...).
        message_thread_id: Topic du message auquel le bouton est attaché
            (None : General) — détermine le clavier racine (topic-projet vs
            General) et le contexte projet des actions directes.
    """
    parts = callback_data.split(":")
    action = parts[1] if len(parts) > 1 else "root"

    if action == "root":
        return _root_screen(config_dir, message_thread_id)

    if action == "new_project":
        result = await execute_tool(
            "new_project", {}, config=SimpleNamespace(config_dir=config_dir),
            bot=bot, chat_id=chat_id,
        )
        text = (
            "Création d'un nouveau projet en cours — donne un nom dans le message suivant."
            if result.status == "ok" else format_tool_result(result)
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[[_BACK_BUTTON]])

    if action in ("new_feature", "archive", "run_feature"):
        if message_thread_id is not None:
            project_name = resolve_project(message_thread_id, load_topic_map(config_dir))
            if project_name is None:
                return _error_screen("Ce topic n'est associé à aucun projet connu.")
            return await _dispatch_project_action(
                action, config_dir, project_name, message_thread_id, bot=bot, chat_id=chat_id,
            )
        return _project_selection_screen(config_dir, action)

    if action == "project" and len(parts) == 4:
        _, _, sub_action, project_name = parts
        thread_id = _safe_project_thread_id(config_dir, project_name)
        return await _dispatch_project_action(
            sub_action, config_dir, project_name, thread_id, bot=bot, chat_id=chat_id,
        )

    if action == "feature" and len(parts) == 4:
        _, _, project_name, feature_name = parts
        thread_id = _safe_project_thread_id(config_dir, project_name)
        return await _start_run_feature(
            config_dir, project_name, feature_name, thread_id, bot=bot, chat_id=chat_id,
        )

    return _error_screen("Action de menu inconnue.")


async def _dispatch_project_action(
    action: str, config_dir: Path, project_name: str, thread_id: Optional[int], *,
    bot: Any, chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    if action == "new_feature":
        return await _start_new_feature(config_dir, project_name, thread_id, bot=bot, chat_id=chat_id)
    if action == "archive":
        return await _start_archive(config_dir, project_name, bot=bot, chat_id=chat_id)
    if action == "run_feature":
        return await _feature_list_screen(config_dir, project_name)
    return _error_screen("Action de menu inconnue.")
