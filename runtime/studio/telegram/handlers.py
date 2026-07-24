"""
Handlers Telegram — dispatch des commandes slash vers le registre d'outils
partagé (ADR 0013, Décision 4).

Tranche S2 : lecture seule uniquement (les 3 outils avec handler réel dans
studio.tools.registry — lire_statut, lire_progression, lister_projets). Pas
de rendu de confirmation (tranche S3), pas de compréhension du langage
naturel par Devaimazing (tranche S4) — tout texte qui n'est pas une commande
slash reconnue est ignoré silencieusement.

La logique de dispatch (handle_slash_command) est volontairement séparée du
câblage aiogram (build_router) : elle ne prend que des types simples en
argument, testable sans construire de vrais objets Message aiogram — voir
runtime/tests/test_telegram_handlers.py.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from aiogram import Router
from aiogram.types import Message

from studio.config import StudioConfig, default_config_dir
from studio.telegram.topics import load_topic_map, resolve_project
from studio.tools.registry import ToolResult, execute_tool, parse_slash_command

# Outils utilisables depuis le topic General, sans résolution de projet
# (lister_projets porte sur config/projects/ dans son ensemble, pas sur un
# projet particulier).
_GENERAL_SCOPE_TOOLS = {"lister_projets"}


async def handle_slash_command(
    text: str,
    *,
    chat_id: int,
    message_thread_id: Optional[int],
    allowed_chat_id: int,
    config_dir: Path,
) -> Optional[str]:
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

    Returns:
        Le texte de réponse à envoyer, ou None si aucune réponse n'est due
        (chat_id non autorisé, ou texte qui n'est pas une commande slash
        reconnue — S4/Devaimazing absent en S2).
    """
    if chat_id != allowed_chat_id:
        return None

    parsed = parse_slash_command(text)
    if parsed is None:
        return None

    tool_name, args = parsed

    if tool_name in _GENERAL_SCOPE_TOOLS:
        config = SimpleNamespace(config_dir=config_dir)
    else:
        topic_map = load_topic_map(config_dir)
        project_name = resolve_project(message_thread_id, topic_map)
        if project_name is None:
            return "Cette commande doit être utilisée dans le topic d'un projet."
        try:
            config = StudioConfig(project_name=project_name, config_dir=config_dir)
        except (FileNotFoundError, ValueError) as exc:
            return f"Configuration du projet {project_name!r} invalide : {exc}"

    result = await execute_tool(tool_name, args, config=config)
    return _format_result(result)


def _format_result(result: ToolResult) -> str:
    if result.status in ("error", "needs_confirmation"):
        return result.summary
    if not result.data:
        return result.summary
    return "\n".join(f"{key} : {value}" for key, value in result.data.items())


def build_router(config_dir: Optional[Path], allowed_chat_id: int) -> Router:
    """
    Construit le Router aiogram, câblage mince autour de handle_slash_command.

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
        reply_text = await handle_slash_command(
            message.text,
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            allowed_chat_id=allowed_chat_id,
            config_dir=resolved_config_dir,
        )
        if reply_text is not None:
            await message.reply(reply_text)

    return router
