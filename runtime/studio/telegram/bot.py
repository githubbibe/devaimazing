"""
Bot Telegram (ADR 0013, tranche S2) — premier process long-running du
dépôt (voir docs/roadmap.md : aucun mécanisme de ce type n'existait avant,
tout le reste de runtime/studio/ est invocation CLI ponctuelle).

L'arrêt propre sur SIGINT/SIGTERM n'est pas géré ici : aiogram le fait
nativement (Dispatcher.start_polling, handle_signals=True par défaut) —
pas de gestion de signal ajoutée dans ce dépôt.
"""

from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher

from studio.config import default_config_dir, load_global_telegram_config
from studio.telegram import menu
from studio.telegram.handlers import build_router
from studio.telegram.pm_dialogue import restore_pending_dialogues
from studio.telegram.topics import load_topic_map

_PLACEHOLDER_PREFIX = "<PLACEHOLDER"


def build_bot_and_dispatcher(config_dir: Optional[Path] = None) -> tuple[Bot, Dispatcher]:
    """
    Construit le Bot et le Dispatcher aiogram à partir de la config globale.

    Args:
        config_dir: Répertoire de config (voir StudioConfig.config_dir).

    Returns:
        (bot, dispatcher) prêts pour dispatcher.start_polling(bot).

    Raises:
        ValueError: Si telegram.token ou telegram.allowed_chat_id n'est pas
            configuré dans config/local.yml (placeholder encore présent
            dans config/studio.yml, voir ADR 0013).
    """
    telegram_config = load_global_telegram_config(config_dir)
    token = telegram_config.get("token")
    allowed_chat_id = telegram_config.get("allowed_chat_id")

    if not token or str(token).startswith(_PLACEHOLDER_PREFIX):
        raise ValueError(
            "telegram.token non configuré — voir config/local.yml (ADR 0013)."
        )
    if not allowed_chat_id or str(allowed_chat_id).startswith(_PLACEHOLDER_PREFIX):
        raise ValueError(
            "telegram.allowed_chat_id non configuré — voir config/local.yml (ADR 0013)."
        )

    # Pas de parse_mode HTML/Markdown (défaut aiogram) : les réponses
    # (studio.telegram.handlers) sont du texte brut non échappé (ex.
    # intervention_reason d'un run peut contenir "<", ">", "&") — un
    # parse_mode actif ferait échouer l'envoi au premier caractère spécial
    # ("can't parse entities").
    bot = Bot(token=str(token))
    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(config_dir=config_dir, allowed_chat_id=int(allowed_chat_id))
    )
    return bot, dispatcher


async def _send_persistent_keyboards(bot, chat_id: int, config_dir: Optional[Path]) -> None:
    """
    Poste un message portant le clavier persistant « Menu → »
    (studio.telegram.menu.persistent_keyboard, remplace la commande tapée
    /menu) dans General et dans chaque topic-projet déjà connu.

    Appelé au démarrage du bot plutôt qu'une seule fois à la création du
    projet (voir new_project_flow, qui l'attache aussi à ses propres
    messages) : garantit la présence du bouton même pour un topic créé
    avant l'introduction de cette fonctionnalité, ou si l'état du clavier
    côté client a été perdu.

    Side effects:
        Envoie un message dans General et dans chaque topic connu (voir
        studio.telegram.topics.load_topic_map).
    """
    resolved_config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
    keyboard = menu.persistent_keyboard()

    await bot.send_message(chat_id, "Bot démarré.", reply_markup=keyboard)
    for thread_id in load_topic_map(resolved_config_dir):
        await bot.send_message(
            chat_id, "Bot démarré.", message_thread_id=thread_id, reply_markup=keyboard,
        )


async def run_bot(config_dir: Optional[Path] = None) -> None:
    """
    Démarre le bot en polling — bloque jusqu'à interruption.

    Args:
        config_dir: Répertoire de config (voir StudioConfig.config_dir).

    Side effects:
        Recharge les dialogues de cadrage PM interrompus par un précédent
        arrêt du bot (voir pm_dialogue.restore_pending_dialogues). Poste un
        message avec le clavier persistant « Menu → » dans General et
        chaque topic connu (voir _send_persistent_keyboards). Ouvre une
        connexion réseau persistante à l'API Telegram (long polling). Se
        termine proprement sur SIGINT/SIGTERM (géré nativement par aiogram)
        ou sur toute exception non catchée par un handler.
    """
    bot, dispatcher = build_bot_and_dispatcher(config_dir)
    try:
        restore_pending_dialogues(config_dir)
        allowed_chat_id = load_global_telegram_config(config_dir)["allowed_chat_id"]
        await _send_persistent_keyboards(bot, int(allowed_chat_id), config_dir)
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
