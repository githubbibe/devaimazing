"""
Handlers Telegram — dispatch des commandes slash ET du langage naturel
(Devaimazing, ADR 0013 tranche S4) vers le même registre d'outils partagé
(Décision 4), avec rendu de la confirmation pour les outils
`requiert_confirmation`.

La logique de dispatch (handle_slash_command, handle_natural_language,
handle_confirmation_callback) est volontairement séparée du câblage aiogram
(build_router) : elle ne prend que des types simples en argument, testable
sans construire de vrais objets Message/CallbackQuery aiogram — voir
runtime/tests/test_telegram_handlers.py.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, Union

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from studio.config import (
    StudioConfig,
    default_config_dir,
    load_global_devaimazing_config,
    load_global_whisper_config,
)
from studio.devaimazing.agent import dispatch_tool_call, interpret_message
from studio.telegram.confirmations import CALLBACK_PREFIX as _CALLBACK_PREFIX
from studio.telegram.confirmations import build_confirmation_keyboard
from studio.telegram.confirmations import pending_confirmations as _pending_confirmations
from studio.telegram import menu
from studio.telegram.new_project_flow import handle_project_name_reply
from studio.telegram.pm_dialogue import handle_dialogue_reply
from studio.telegram.run_flow import handle_run_reply
from studio.telegram.topics import load_topic_map, resolve_project
from studio.tools.registry import (
    TOOL_REGISTRY,
    execute_tool,
    format_tool_result,
    parse_slash_command,
)
from studio.tools.whisper import ExternalServiceError, transcribe_voice_message

# Outils utilisables depuis le topic General, sans résolution de projet par
# le topic — le nom du projet vient de l'argument de la commande elle-même
# (voir ADR 0013, Décision 4 : /new et /archive sont des commandes General).
_GENERAL_SCOPE_TOOLS = {"lister_projets", "creer_projet", "archive_projet", "new_project"}


def _is_stop_command(text: str) -> bool:
    """/stop a priorité absolue sur tout état en attente (ADR 0015,
    Décision 6) — voir son usage dans _on_message."""
    parsed = parse_slash_command(text)
    return parsed is not None and parsed[0] == "stop_run"


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


def _resolve_config_for_tool(
    tool_name: str, message_thread_id: Optional[int], config_dir: Path,
) -> Union[Any, HandlerReply]:
    """
    Résout la config à transmettre à execute_tool/dispatch_tool_call pour un
    outil donné — partagé entre handle_slash_command et
    handle_natural_language (même règle de résolution : le nom d'outil,
    identifié différemment selon le canal, détermine si une résolution de
    projet par topic est nécessaire).

    Returns:
        Une config utilisable (StudioConfig ou SimpleNamespace pour les
        outils General-scope), ou un HandlerReply d'erreur à renvoyer tel
        quel si la résolution échoue (aucun outil n'est appelé dans ce cas).

    Notes:
        Un nom d'outil absent de TOOL_REGISTRY (halluciné par Devaimazing,
        voir docs/roadmap.md) est traité comme General-scope plutôt que de
        tenter une résolution de projet par topic : sans ce court-circuit,
        un nom inconnu depuis General produirait le message trompeur
        « utilisez le topic d'un projet » au lieu du vrai message
        « outil inconnu » qu'execute_tool produit normalement — la
        validation du nom reste entièrement déléguée à execute_tool, cette
        fonction ne fait que décider s'il faut résoudre un projet avant.
    """
    if tool_name in _GENERAL_SCOPE_TOOLS or tool_name not in TOOL_REGISTRY:
        return SimpleNamespace(config_dir=config_dir)

    topic_map = load_topic_map(config_dir)
    project_name = resolve_project(message_thread_id, topic_map)
    if project_name is None:
        return HandlerReply("Cette commande doit être utilisée dans le topic d'un projet.")
    try:
        return StudioConfig(project_name=project_name, config_dir=config_dir)
    except (FileNotFoundError, ValueError) as exc:
        return HandlerReply(f"Configuration du projet {project_name!r} invalide : {exc}")


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

    if tool_name == "archive_projet" and "name" not in args and message_thread_id is not None:
        # /archive sans argument depuis un topic-projet (ADR 0015,
        # Décision 5) : le topic donne le contexte, pas besoin de retaper
        # le nom du projet (déjà requis, lui, depuis General — voir
        # parse_slash_command, aucun mot après la commande ici).
        project_name = resolve_project(message_thread_id, load_topic_map(config_dir))
        if project_name is None:
            return HandlerReply("Ce topic n'est associé à aucun projet connu.")
        args = {**args, "name": project_name}

    config = _resolve_config_for_tool(tool_name, message_thread_id, config_dir)
    if isinstance(config, HandlerReply):
        return config

    result = await execute_tool(
        tool_name, args, config=config, bot=bot, chat_id=chat_id,
        message_thread_id=message_thread_id,
    )

    if result.status == "needs_confirmation":
        confirmation_id = uuid.uuid4().hex[:12]
        _pending_confirmations[confirmation_id] = (tool_name, args, config)
        return HandlerReply(result.summary, confirmation_id=confirmation_id)

    return HandlerReply(format_tool_result(result))


async def resolve_message_text(
    *,
    text: Optional[str],
    voice_file_id: Optional[str],
    audio_file_id: Optional[str],
    bot: Any,
    config_dir: Path,
) -> Optional[str]:
    """
    Résout le texte à traiter à partir d'un message Telegram (ADR 0014).

    Si `text` est déjà fourni (message tapé), le renvoie tel quel — pas
    d'appel Whisper. Sinon, si un fichier vocal/audio est présent, le
    télécharge et le transcrit via `tools.whisper.transcribe_voice_message`.
    Au-delà de cette fonction, Devaimazing ne fait plus AUCUNE différence
    entre texte tapé et texte transcrit (voir ADR 0014, prompts/devaimazing.md
    section « Origine des messages ») — c'est la seule fonction du dépôt qui
    connaît la distinction.

    Args:
        text: `message.text` (None si le message n'est pas du texte tapé).
        voice_file_id: `message.voice.file_id` (None si pas de message vocal).
        audio_file_id: `message.audio.file_id` (None si pas de fichier audio).
        bot: Contexte Telegram (aiogram.Bot ou équivalent testable exposant
            `download(file_id) -> BinaryIO`).
        config_dir: Répertoire de config (pour load_global_whisper_config).

    Returns:
        Le texte à traiter, ou None si rien n'est exploitable (ni texte, ni
        vocal/audio — ex. sticker, photo) OU si la transcription échoue
        (téléchargement impossible, serveur whisper.cpp injoignable, aucune
        parole détectée) : ce cas de figure n'est PAS distingué de "rien à
        traiter" par le type de retour — c'est à l'appelant (build_router)
        de décider s'il doit prévenir l'utilisateur, en sachant lui-même si
        un fichier vocal/audio était présent (voir _on_message).
    """
    if text:
        return text

    file_id = voice_file_id or audio_file_id
    if file_id is None or bot is None:
        return None

    try:
        buffer = await bot.download(file_id)
    except TelegramAPIError:
        return None
    if buffer is None:
        return None

    whisper_config = load_global_whisper_config(config_dir)
    try:
        transcript = await transcribe_voice_message(
            buffer.read(),
            base_url=whisper_config["base_url"],
            language=whisper_config["language"],
        )
    except (ExternalServiceError, TimeoutError):
        return None

    return transcript or None


async def handle_natural_language(
    text: str,
    *,
    chat_id: int,
    message_thread_id: Optional[int],
    allowed_chat_id: int,
    config_dir: Path,
    bot: Any = None,
) -> Optional[HandlerReply]:
    """
    Traite un message texte Telegram qui n'est PAS une commande slash, via
    l'agent Devaimazing (ADR 0013, tranche S4).

    À la différence de handle_slash_command, le nom d'outil (donc le projet
    concerné) n'est connu qu'*après* l'appel LLM (interpret_message) — la
    résolution de config est donc faite ici, après coup, avec la même règle
    que handle_slash_command (_resolve_config_for_tool), pas en amont.

    Args:
        Voir handle_slash_command — mêmes arguments, même sémantique.

    Returns:
        HandlerReply à envoyer, ou None si aucune réponse n'est due (chat_id
        non autorisé, ou models.devaimazing non configuré dans studio.yml —
        feature non activée sur cette installation).
    """
    if chat_id != allowed_chat_id:
        return None

    llm_config = load_global_devaimazing_config(config_dir)
    if not llm_config["model"]:
        return None

    reply, tool_call = await interpret_message(
        text,
        model=llm_config["model"],
        base_url=llm_config["base_url"],
        num_ctx=llm_config["num_ctx"],
    )

    if tool_call is None:
        return HandlerReply(reply)

    tool_name, args = tool_call["name"], tool_call["arguments"]
    config = _resolve_config_for_tool(tool_name, message_thread_id, config_dir)
    if isinstance(config, HandlerReply):
        return config

    text_out, pending = await dispatch_tool_call(
        tool_name, args, config=config, bot=bot, chat_id=chat_id,
        message_thread_id=message_thread_id,
    )

    if pending is not None:
        confirmation_id = uuid.uuid4().hex[:12]
        _pending_confirmations[confirmation_id] = (pending[0], pending[1], config)
        return HandlerReply(text_out, confirmation_id=confirmation_id)

    return HandlerReply(text_out)


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
        if message.from_user is not None and message.from_user.is_bot:
            # Aucune commande slash n'a de sens venant d'un bot, mais
            # surtout : sans ce garde-fou, tout message de bot déclencherait
            # un appel LLM via handle_natural_language (voir plus bas) —
            # coûteux et risque de boucle si un autre bot du groupe répond
            # aux messages de celui-ci. Telegram ne renvoie normalement pas
            # les messages envoyés par le bot lui-même à son propre handler,
            # mais rien ne garantit l'absence d'un autre bot dans le groupe.
            return
        if message.chat.id != allowed_chat_id:
            return

        if message.text:
            if _is_stop_command(message.text):
                # /stop a priorité absolue (ADR 0015, Décision 6) : ne doit
                # JAMAIS être intercepté comme réponse à un nom de projet
                # attendu, un dialogue de cadrage, ou un run en attente de
                # validation humaine — sinon il ne pourrait jamais
                # interrompre exactement les situations qu'il sert à
                # interrompre. Court-circuite donc la chaîne de priorité
                # normale ci-dessous plutôt que de s'y insérer.
                reply = await handle_slash_command(
                    message.text,
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    allowed_chat_id=allowed_chat_id,
                    config_dir=resolved_config_dir,
                    bot=message.bot,
                )
                if reply is not None:
                    keyboard = (
                        build_confirmation_keyboard(reply.confirmation_id)
                        if reply.confirmation_id is not None else None
                    )
                    await message.reply(reply.text, reply_markup=keyboard)
                return

            # Un nom de projet attendu dans General (/new_project, ADR 0015)
            # absorbe le message en priorité, avant même un dialogue de
            # cadrage PM (les deux ne peuvent pas être simultanément en
            # attente pour le même chat_id/topic — General vs topic-projet).
            if await handle_project_name_reply(
                message.bot, message.chat.id, message.message_thread_id, message.text,
            ):
                return

            # Un dialogue de cadrage PM en attente dans ce topic (/new_feature,
            # /new_project, ADR 0015) absorbe le message en priorité : c'est
            # la réponse de l'utilisateur au PM, pas une nouvelle commande —
            # même sémantique que le input() du dialogue terminal
            # (studio.nodes.pm), tout ce qui est tapé est la réponse, y
            # compris si ça ressemble à une commande slash.
            if await handle_dialogue_reply(
                message.bot, message.chat.id, message.message_thread_id, message.text,
            ):
                return

            # Un run en attente de validation humaine dans ce topic (/run,
            # ADR 0015, Décision 4) absorbe le message en priorité : n'importe
            # quel texte reçu ici déclenche la reprise (voir
            # studio.telegram.run_flow.handle_run_reply), le contenu n'est
            # pas lu — placé après handle_dialogue_reply : un topic n'a
            # jamais les deux états en attente simultanément (le dialogue de
            # cadrage est déjà terminé avant qu'un /run ne soit possible).
            if await handle_run_reply(
                message.bot, message.chat.id, message.message_thread_id, message.text,
            ):
                return

            if message.text == "/menu":
                # Pas de priorité absolue comme /stop : reste soumis à
                # l'interception normale ci-dessus par un dialogue/run en
                # attente (ADR 0015, Décision 7 — pure navigation UI, pas
                # d'urgence à faire valoir).
                await menu.send_root_menu(
                    message.bot, message.chat.id, message.message_thread_id, resolved_config_dir,
                )
                return

            reply = await handle_slash_command(
                message.text,
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                allowed_chat_id=allowed_chat_id,
                config_dir=resolved_config_dir,
                bot=message.bot,
            )
            if reply is None and parse_slash_command(message.text) is None:
                # Pas une commande slash (reconnue ou non) : tenter la
                # compréhension du langage naturel par Devaimazing (ADR
                # 0013, tranche S4) plutôt que d'ignorer silencieusement.
                reply = await handle_natural_language(
                    message.text,
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    allowed_chat_id=allowed_chat_id,
                    config_dir=resolved_config_dir,
                    bot=message.bot,
                )
        elif message.voice is not None or message.audio is not None:
            # Message vocal/audio (ADR 0014) : jamais interprété comme une
            # commande slash (une commande slash n'a pas vocation à être
            # dictée à l'oral, voir ADR 0014) — transcrit puis traité
            # exactement comme un message texte natif par Devaimazing.
            text = await resolve_message_text(
                text=None,
                voice_file_id=message.voice.file_id if message.voice else None,
                audio_file_id=message.audio.file_id if message.audio else None,
                bot=message.bot,
                config_dir=resolved_config_dir,
            )
            if text is None:
                await message.reply(
                    "Je n'ai pas réussi à transcrire ce message vocal, "
                    "peux-tu réessayer ou taper ton message ?"
                )
                return
            reply = await handle_natural_language(
                text,
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                allowed_chat_id=allowed_chat_id,
                config_dir=resolved_config_dir,
                bot=message.bot,
            )
        else:
            return

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
            # Retour à l'affichage du menu une fois la confirmation résolue
            # (ADR 0015, Décision 7) — générique à tout outil confirmable
            # (archive_projet, valider_fiche_feature, valider_fiche_projet),
            # pas de logique par-outil.
            in_topic = callback.message.message_thread_id is not None
            await callback.message.edit_text(
                reply_text, reply_markup=menu.build_root_keyboard(in_topic=in_topic),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{menu.CALLBACK_PREFIX}:"))
    async def _on_menu_callback(callback: CallbackQuery) -> None:
        chat_id = callback.message.chat.id if callback.message else None
        thread_id = callback.message.message_thread_id if callback.message else None
        if chat_id is None or callback.data is None or chat_id != allowed_chat_id:
            await callback.answer()
            return
        text, keyboard = await menu.handle_menu_callback(
            callback.data, chat_id=chat_id, message_thread_id=thread_id,
            config_dir=resolved_config_dir, bot=callback.bot,
        )
        if callback.message is not None:
            await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()

    return router
