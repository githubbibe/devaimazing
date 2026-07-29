"""
Handlers Telegram — dispatch des commandes slash vers le registre d'outils
partagé (ADR 0013, Décision 4), et rendu de la confirmation pour les outils
`requiert_confirmation` (tranche S3 : `archive_projet` — `creer_projet`
n'en requiert pas).

Pas de compréhension du langage naturel par Devaimazing (tranche S4) — tout
texte qui n'est pas une commande slash reconnue est ignoré silencieusement.

La logique de dispatch (handle_slash_command, handle_confirmation_callback)
est volontairement séparée du câblage aiogram (build_router) : elle ne
prend que des types simples en argument, testable sans construire de vrais
objets Message/CallbackQuery aiogram — voir
runtime/tests/test_telegram_handlers.py.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from studio.config import StudioConfig, default_config_dir
from studio.telegram.topics import load_topic_map, resolve_project
from studio.tools.registry import execute_tool, format_tool_result, parse_slash_command

# Outils utilisables depuis le topic General, sans résolution de projet par
# le topic — le nom du projet vient de l'argument de la commande elle-même
# (voir ADR 0013, Décision 4 : /new et /archive sont des commandes General).
_GENERAL_SCOPE_TOOLS = {"lister_projets", "creer_projet", "archive_projet"}

_CALLBACK_PREFIX = "confirm"

# Confirmations en attente : mémoire du process, jamais persistées (cohérent
# avec l'absence de checkpointer dédié pour Devaimazing, voir ADR 0013,
# Décision 3) — perdues si le bot redémarre entre la question et la
# réponse ; un Oui tapé après un redémarrage retombe sur "confirmation
# expirée", à retaper depuis la commande.
_pending_confirmations: dict[str, tuple[str, dict[str, Any], Any]] = {}


@dataclass
class HandlerReply:
    """
    Réponse produite par handle_slash_command.

    Args:
        text: Texte à envoyer.
        confirmation_id: Si non None, la réponse doit être accompagnée d'un
            clavier Oui/Non (voir build_confirmation_keyboard) — l'outil
            requiert une confirmation, son handler n'a pas encore été
            appelé.
    """

    text: str
    confirmation_id: Optional[str] = None


async def handle_slash_command(
    text: str,
    *,
    chat_id: int,
    message_thread_id: Optional[int],
    allowed_chat_id: int,
    config_dir: Path,
    bot: Any = None,
) -> Optional[HandlerReply]:
    """
    Traite un message texte Telegram et produit la réponse à envoyer.

    Args:
        text: Texte brut du message.
        chat_id: chat_id du message reçu.
        message_thread_id: Identifiant du topic (None : General, ou groupe
            sans topics activés).
        allowed_chat_id: chat_id autorisé (ADR 0013, Décision 2) — restreint
            au niveau du groupe, pas par utilisateur individuel
            (from_user.id) : l'ADR décrit explicitement un studio
            mono-utilisateur, le groupe entier est donc la frontière visée,
            pas chaque membre. À revoir explicitement si le groupe cesse
            d'être mono-utilisateur.
        config_dir: Répertoire de config (voir StudioConfig.config_dir).
        bot: Contexte Telegram transmis à execute_tool — nécessaire pour
            les outils sans confirmation qui appellent quand même l'API
            Telegram (ex. creer_projet : requiert_confirmation=False, donc
            son handler s'exécute dès ce premier appel à execute_tool, pas
            au moment d'une confirmation). None si non fourni (outils qui
            n'en ont pas besoin).

    Returns:
        HandlerReply à envoyer, ou None si aucune réponse n'est due (chat_id
        non autorisé, ou texte qui n'est pas une commande slash reconnue —
        S4/Devaimazing absent).
    """
    if chat_id != allowed_chat_id:
        return None

    parsed = parse_slash_command(text)
    if parsed is None:
        return None

    tool_name, args = parsed

    if tool_name in _GENERAL_SCOPE_TOOLS:
        config: Any = SimpleNamespace(config_dir=config_dir)
    else:
        topic_map = load_topic_map(config_dir)
        project_name = resolve_project(message_thread_id, topic_map)
        if project_name is None:
            return HandlerReply("Cette commande doit être utilisée dans le topic d'un projet.")
        try:
            config = StudioConfig(project_name=project_name, config_dir=config_dir)
        except (FileNotFoundError, ValueError) as exc:
            return HandlerReply(f"Configuration du projet {project_name!r} invalide : {exc}")

    result = await execute_tool(tool_name, args, config=config, bot=bot, chat_id=chat_id)

    if result.status == "needs_confirmation":
        confirmation_id = uuid.uuid4().hex[:12]
        _pending_confirmations[confirmation_id] = (tool_name, args, config)
        return HandlerReply(result.summary, confirmation_id=confirmation_id)

    return HandlerReply(format_tool_result(result))


async def handle_confirmation_callback(
    callback_data: str,
    *,
    chat_id: int,
    allowed_chat_id: int,
    bot: Any,
) -> Optional[str]:
    """
    Traite le tap sur un bouton Oui/Non d'une confirmation.

    Args:
        callback_data: Donnée du callback ("confirm:<id>:yes|no").
        chat_id: chat_id du message d'origine.
        allowed_chat_id: chat_id autorisé (voir handle_slash_command).
        bot: Contexte Telegram transmis à execute_tool.

    Returns:
        Le texte de remplacement du message de confirmation, ou None si ce
        callback n'est pas de notre ressort (chat non autorisé, préfixe
        inconnu — laisse la place à d'autres handlers de callback futurs).
    """
    if chat_id != allowed_chat_id:
        return None
    if not callback_data.startswith(f"{_CALLBACK_PREFIX}:"):
        return None

    _, confirmation_id, decision = callback_data.split(":", 2)
    pending = _pending_confirmations.pop(confirmation_id, None)
    if pending is None:
        return "Confirmation expirée ou déjà traitée."

    tool_name, args, config = pending
    if decision != "yes":
        return "Annulé."

    result = await execute_tool(
        tool_name, args, config=config, confirmed=True, bot=bot, chat_id=allowed_chat_id,
    )
    return format_tool_result(result)


def build_confirmation_keyboard(confirmation_id: str) -> InlineKeyboardMarkup:
    """Clavier Oui/Non attaché au message de confirmation."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Oui", callback_data=f"{_CALLBACK_PREFIX}:{confirmation_id}:yes"
        ),
        InlineKeyboardButton(
            text="Non", callback_data=f"{_CALLBACK_PREFIX}:{confirmation_id}:no"
        ),
    ]])


def build_router(config_dir: Optional[Path], allowed_chat_id: int) -> Router:
    """
    Construit le Router aiogram, câblage mince autour de handle_slash_command
    et handle_confirmation_callback.

    Args:
        config_dir: Répertoire de config (voir StudioConfig.config_dir),
            None pour le défaut (config/ du dépôt).
        allowed_chat_id: chat_id autorisé (ADR 0013, Décision 2).

    Returns:
        Router prêt à être enregistré sur un Dispatcher (studio.telegram.bot).
    """
    resolved_config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
    router = Router()

    @router.message()
    async def _on_message(message: Message) -> None:
        if not message.text:
            return
        reply = await handle_slash_command(
            message.text,
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            allowed_chat_id=allowed_chat_id,
            config_dir=resolved_config_dir,
            bot=message.bot,
        )
        if reply is None:
            return
        keyboard = (
            build_confirmation_keyboard(reply.confirmation_id)
            if reply.confirmation_id is not None else None
        )
        await message.reply(reply.text, reply_markup=keyboard)

    @router.callback_query(F.data.startswith(f"{_CALLBACK_PREFIX}:"))
    async def _on_callback(callback: CallbackQuery) -> None:
        chat_id = callback.message.chat.id if callback.message else None
        if chat_id is None or callback.data is None:
            await callback.answer()
            return
        reply_text = await handle_confirmation_callback(
            callback.data, chat_id=chat_id, allowed_chat_id=allowed_chat_id, bot=callback.bot,
        )
        if reply_text is not None and callback.message is not None:
            await callback.message.edit_text(reply_text)
        await callback.answer()

    return router
