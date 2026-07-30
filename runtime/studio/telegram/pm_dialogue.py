"""
Dialogue de cadrage PM porté sur Telegram (ADR 0015, phase 1 d'implémentation
— /new_feature uniquement, voir l'ADR pour /new_project et la fiche projet,
laissés à une phase ultérieure).

Équivalent asynchrone, message par message dans un topic Telegram, du
mécanisme terminal input()/print() de studio.nodes.pm._run_validation_dialogue
— réutilise studio.nodes.pm.run_pm_turn (appel Claude Code CLI + parsing
QUESTION:/FICHE_VALIDEE:, canal-agnostique) pour ne pas dupliquer cette
logique entre les deux canaux.

État en mémoire process, jamais persisté (même choix assumé que
_pending_confirmations, voir studio.telegram.confirmations et ADR 0013,
Décision 3) : perdu si le bot redémarre en cours de dialogue, à reprendre
avec /new_feature depuis le début.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from studio.config import StudioConfig
from studio.nodes.pm import run_pm_turn
from studio.state import Phase
from studio.telegram.confirmations import build_confirmation_keyboard, pending_confirmations
from studio.tools.registry import execute_tool, format_tool_result
from studio.tools.tracer import RunTracer

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "pm.md"
_MODEL_KEY = "pm_opus"


@dataclass
class _DialogueState:
    run_id: str
    config: StudioConfig
    system_prompt: str
    transcript: list[str]


# Clé = message_thread_id (un topic = un dialogue en cours au plus, cohérent
# avec un topic = un projet, ADR 0013 Décision 2).
_pending_dialogues: dict[int, _DialogueState] = {}


def _generate_run_id() -> str:
    """Même format que cli.py::_generate_run_id — dupliqué plutôt qu'importé
    de cli.py pour ne pas faire dépendre ce module (bas niveau) du point
    d'entrée CLI (haut niveau)."""
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


async def _present_draft(
    bot: Any, chat_id: int, message_thread_id: int, state: _DialogueState, draft: str,
) -> None:
    """
    Poste le brouillon de fiche produit par le PM et déclenche sa
    confirmation Oui/Non — réutilise le même mécanisme que les outils
    destructifs du registre (execute_tool + pending_confirmations +
    build_confirmation_keyboard), au lieu d'une réponse tapée comme en CLI.
    """
    args = {"run_id": state.run_id, "content": draft}
    result = await execute_tool("valider_fiche_feature", args, config=state.config)

    if result.status == "needs_confirmation":
        confirmation_id = uuid.uuid4().hex[:12]
        pending_confirmations[confirmation_id] = ("valider_fiche_feature", args, state.config)
        await bot.send_message(
            chat_id,
            f"{draft}\n\n{result.summary}",
            message_thread_id=message_thread_id,
            reply_markup=build_confirmation_keyboard(confirmation_id),
        )
        return

    # Ne devrait pas arriver avec la classification actuelle de
    # valider_fiche_feature (requiert_confirmation=True) — couvert pour
    # rester correct si elle change un jour.
    await bot.send_message(
        chat_id, format_tool_result(result), message_thread_id=message_thread_id,
    )


async def start_feature_dialogue(
    bot: Any, chat_id: int, message_thread_id: int, config: StudioConfig,
) -> None:
    """
    Démarre le dialogue de cadrage PM pour une nouvelle feature dans ce
    topic-projet (/new_feature, ADR 0015). Poste la première question (ou,
    cas limite, présente directement un brouillon si le PM n'en pose aucune)
    et enregistre l'état en attente de la prochaine réponse de l'utilisateur
    (voir handle_dialogue_reply).

    Args:
        bot: Bot aiogram (ou équivalent testable exposant send_message).
        chat_id: chat_id du groupe (ADR 0013, Décision 2 — mono-groupe).
        message_thread_id: topic du projet concerné, sert de clé à l'état en
            attente.
        config: StudioConfig du projet déjà résolu par l'appelant (voir
            tools.registry._handle_new_feature) — cette fonction ne refait
            aucune résolution de topic/projet.

    Side effects:
        Envoie un message dans le topic. Enregistre un état en mémoire
        process (voir _pending_dialogues).
    """
    run_id = _generate_run_id()
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    transcript = [
        "Objectif initial de l'utilisateur : (non précisé — nouvelle feature "
        "démarrée depuis Telegram, /new_feature)."
    ]
    tracer = RunTracer.for_run(config, run_id).for_agent("pm", Phase.CADRAGE)

    turn, *_ = await run_pm_turn(config, tracer, system_prompt, transcript, _MODEL_KEY)

    if turn.kind == "question":
        transcript.append(f"PM : {turn.text}")
        _pending_dialogues[message_thread_id] = _DialogueState(
            run_id=run_id, config=config, system_prompt=system_prompt, transcript=transcript,
        )
        await bot.send_message(chat_id, turn.text, message_thread_id=message_thread_id)
        return

    state = _DialogueState(
        run_id=run_id, config=config, system_prompt=system_prompt, transcript=transcript,
    )
    await _present_draft(bot, chat_id, message_thread_id, state, turn.text)


async def handle_dialogue_reply(
    bot: Any, chat_id: int, message_thread_id: Optional[int], text: str,
) -> bool:
    """
    Traite un message tapé dans un topic où un dialogue de cadrage PM est en
    attente (voir start_feature_dialogue) — à appeler par
    telegram.handlers._on_message AVANT le dispatch slash/langage naturel
    habituel : un message dans ce contexte est la réponse de l'utilisateur
    au PM, pas une nouvelle commande (même sémantique que le input() du
    dialogue terminal — tout ce qui est tapé est la réponse).

    Args:
        message_thread_id: None si le message vient de General (aucun
            dialogue n'y est jamais enregistré, /new_feature est
            topic-projet uniquement dans cette phase) — retourne False
            immédiatement dans ce cas.

    Returns:
        True si un dialogue était en attente pour ce topic et que ce message
        a été consommé comme réponse (l'appelant ne doit rien faire de plus).
        False sinon (aucun dialogue en attente ici — l'appelant continue son
        dispatch normal).
    """
    if message_thread_id is None:
        return False

    state = _pending_dialogues.get(message_thread_id)
    if state is None:
        return False

    state.transcript.append(f"Utilisateur : {text}")
    await bot.send_chat_action(chat_id, "typing", message_thread_id=message_thread_id)

    tracer = RunTracer.for_run(state.config, state.run_id).for_agent("pm", Phase.CADRAGE)
    turn, *_ = await run_pm_turn(
        state.config, tracer, state.system_prompt, state.transcript, _MODEL_KEY,
    )

    if turn.kind == "question":
        state.transcript.append(f"PM : {turn.text}")
        await bot.send_message(chat_id, turn.text, message_thread_id=message_thread_id)
        return True

    del _pending_dialogues[message_thread_id]
    await _present_draft(bot, chat_id, message_thread_id, state, turn.text)
    return True
