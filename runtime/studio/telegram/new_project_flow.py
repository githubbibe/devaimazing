"""
Orchestration de `/new_project` (ADR 0015, phase 2 d'implémentation) —
demande le nom du projet, puis exécute dans l'ordre : config projet, topic
Telegram (nécessaire en premier pour que les messages de progression
suivants aient un endroit où être postés — voir ADR 0015, Décision 3),
dossier + repo local, repo distant, puis lance le dialogue de cadrage PM
(studio.telegram.pm_dialogue.start_project_dialogue).

Purement déterministe (aucun appel LLM) jusqu'au dernier point — le dialogue
PM lui-même est délégué à pm_dialogue, pas dupliqué ici.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from studio.config import StudioConfig
from studio.telegram import menu
from studio.telegram.pm_dialogue import start_project_dialogue
from studio.tools.git import create_github_remote, create_initial_commit, init_repo, push_branch
from studio.tools.project_config import write_project_config
from studio.tools.registry import execute_tool

_PROJECTS_ROOT = Path("~/code/aimazing").expanduser()

# En attente du nom d'un nouveau projet : mémoire process, même pattern que
# pm_dialogue._pending_dialogues / telegram.confirmations.pending_confirmations
# — clé = chat_id (General n'a pas de message_thread_id, à la différence des
# dialogues de cadrage qui vivent tous dans un topic-projet).
_pending_project_name: dict[int, Path] = {}


async def start_new_project_flow(bot: Any, chat_id: int, config_dir: Path) -> None:
    """
    Amorce /new_project : demande le nom, enregistre l'attente de la
    réponse (voir handle_project_name_reply).

    Args:
        bot: Bot aiogram (ou équivalent testable exposant send_message).
        chat_id: chat_id du groupe (ADR 0013, Décision 2 — mono-groupe).
        config_dir: Répertoire de config (StudioConfig.config_dir) — retenu
            pour la suite du flux, puisque handle_project_name_reply n'a pas
            d'autre moyen de le retrouver.

    Side effects:
        Envoie un message dans General. Enregistre un état en mémoire
        process (voir _pending_project_name).
    """
    _pending_project_name[chat_id] = Path(config_dir)
    await bot.send_message(
        chat_id, "Donne un nom pour le nouveau projet.",
        reply_markup=menu.persistent_keyboard(),
    )


async def handle_project_name_reply(
    bot: Any, chat_id: int, message_thread_id: Optional[int], text: str,
) -> bool:
    """
    Traite un message dans General alors qu'un nom de projet est attendu
    (voir start_new_project_flow) — à appeler par telegram.handlers._on_message
    AVANT handle_dialogue_reply (voir sa docstring : même principe, priorité
    à l'état en attente sur le dispatch slash/langage naturel habituel).

    Args:
        message_thread_id: None attendu (General) — retourne False sinon
            (le nom d'un projet ne se donne jamais depuis un topic-projet).

    Returns:
        True si un nom était attendu pour ce chat et que ce message a été
        consommé comme réponse (l'appelant ne doit rien faire de plus).
        False sinon.
    """
    if message_thread_id is not None:
        return False

    config_dir = _pending_project_name.pop(chat_id, None)
    if config_dir is None:
        return False

    name = text.strip()
    config_path = config_dir / "projects" / f"{name}.yml"
    if config_path.exists():
        await bot.send_message(
            chat_id, f"config/projects/{name}.yml existe déjà — projet déjà initialisé.",
        )
        return True

    target = _PROJECTS_ROOT / name
    write_project_config(config_path, name, target)

    topic_result = await execute_tool(
        "creer_projet", {"name": name},
        config=SimpleNamespace(config_dir=config_dir), bot=bot, chat_id=chat_id,
    )
    if topic_result.status != "ok":
        await bot.send_message(chat_id, topic_result.summary)
        return True
    thread_id = topic_result.data["thread_id"]

    await bot.send_message(
        chat_id, f"Le projet {name} est en cours de création.",
        message_thread_id=thread_id, reply_markup=menu.persistent_keyboard(),
    )

    target.mkdir(parents=True)
    await init_repo(target, initial_branch="develop")
    await create_initial_commit(target, name)
    await bot.send_message(
        chat_id, f"Le dossier {name} a été créé.", message_thread_id=thread_id,
    )
    await bot.send_message(
        chat_id, "Le repo local a été créé.", message_thread_id=thread_id,
    )

    if shutil.which("gh") is None:
        await bot.send_message(
            chat_id,
            "gh introuvable dans PATH — repo GitHub non créé, à faire manuellement si besoin.",
            message_thread_id=thread_id,
        )
    else:
        await create_github_remote(target, name, private=True)
        await push_branch(target, "develop")
        await bot.send_message(
            chat_id, "Le repo distant a été créé.", message_thread_id=thread_id,
        )

    await bot.send_message(
        chat_id,
        "Le Project Manager attend maintenant une description du nouveau projet.",
        message_thread_id=thread_id,
    )

    project_config = StudioConfig(project_name=name, config_dir=config_dir)
    await start_project_dialogue(bot, chat_id, thread_id, project_config, name)
    return True
