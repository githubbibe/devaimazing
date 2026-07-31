"""
Dialogue de cadrage PM porté sur Telegram (ADR 0015) — deux variantes,
`/new_feature` (phase 1 d'implémentation) et `/new_project` (phase 2) :

Équivalent asynchrone, message par message dans un topic Telegram, du
mécanisme terminal input()/print() de studio.nodes.pm._run_validation_dialogue
— réutilise studio.nodes.pm.run_pm_turn (appel Claude Code CLI + parsing
QUESTION:/FICHE_VALIDEE:, canal-agnostique) pour ne pas dupliquer cette
logique entre les deux canaux, ni entre les deux variantes (feature/projet)
ci-dessous — seuls le seed de transcript et l'outil de validation finale
diffèrent (voir _DialogueState.kind).

État tenu en mémoire process (comme _pending_confirmations, voir
studio.telegram.confirmations et ADR 0013, Décision 3), mais le transcript
est en plus persisté sur disque (_DIALOGUES_STATE_DIR, un fichier JSON par
message_thread_id) à chaque tour — contrairement au reste de l'état en
attente de ce dépôt (confirmations, run_flow._active_runs), qui reste
volontairement en mémoire pure : un dialogue de cadrage peut s'étendre sur
plusieurs tours espacés dans le temps (l'utilisateur répond quand il veut),
le perdre au moindre redémarrage du bot forçait à tout retaper depuis le
début — gap trouvé en usage réel (webaimazing-v2, voir docs/roadmap.md).
restore_pending_dialogues() recharge ces fichiers au démarrage du bot (voir
studio.telegram.bot.run_bot). Le fichier est supprimé dès que le dialogue
sort de sa phase "questions" (brouillon présenté ou /stop) — la
confirmation Oui/Non qui suit un brouillon présenté, elle, reste en mémoire
pure (pending_confirmations), non couverte ici.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from studio.config import StudioConfig
from studio.nodes.pm import build_cadrage_system_prompt, run_pm_turn
from studio.state import Phase
from studio.telegram.confirmations import build_confirmation_keyboard, pending_confirmations
from studio.tools.registry import execute_tool, format_tool_result
from studio.tools.tracer import RunTracer

_MODEL_KEY = "pm_opus"
_DIALOGUES_STATE_DIR = Path("~/.devaimazing/dialogues").expanduser()
_CONFIG_RESOLUTION_ERRORS = (FileNotFoundError, ValueError)


@dataclass
class _DialogueState:
    kind: Literal["feature", "project"]
    # feature : run_id (specs/<run_id>/card-root.md, voir valider_fiche_feature).
    # project : identifiant de trace seulement (specs/<trace_id>/trace.jsonl) —
    # une fiche projet n'est pas run-scopée, ce n'est pas un vrai run_id.
    trace_id: str
    config: StudioConfig
    system_prompt: str
    transcript: list[str]


# Clé = message_thread_id (un topic = un dialogue en cours au plus, cohérent
# avec un topic = un projet, ADR 0013 Décision 2).
_pending_dialogues: dict[int, _DialogueState] = {}


def _dialogue_state_path(message_thread_id: int) -> Path:
    return _DIALOGUES_STATE_DIR / f"{message_thread_id}.json"


def _persist_dialogue_state(message_thread_id: int, state: _DialogueState) -> None:
    path = _dialogue_state_path(message_thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "kind": state.kind,
            "trace_id": state.trace_id,
            "project_name": state.config.project_name,
            "transcript": state.transcript,
        }),
        encoding="utf-8",
    )


def _delete_persisted_dialogue_state(message_thread_id: int) -> None:
    _dialogue_state_path(message_thread_id).unlink(missing_ok=True)


def restore_pending_dialogues(config_dir: Optional[Path] = None) -> None:
    """
    Recharge en mémoire les dialogues de cadrage interrompus par un
    redémarrage du bot (voir _persist_dialogue_state) — à appeler une fois
    au démarrage (studio.telegram.bot.run_bot), avant de commencer à traiter
    des messages.

    Args:
        config_dir: Répertoire de config (voir StudioConfig.config_dir),
            None pour le défaut (config/ du dépôt).

    Side effects:
        Peuple _pending_dialogues à partir de _DIALOGUES_STATE_DIR. Un
        fichier dont le projet n'existe plus (config supprimée/renommée) ou
        au contenu invalide est ignoré silencieusement plutôt que de faire
        planter le démarrage du bot.
    """
    if not _DIALOGUES_STATE_DIR.is_dir():
        return

    system_prompt = build_cadrage_system_prompt()
    for path in sorted(_DIALOGUES_STATE_DIR.glob("*.json")):
        try:
            message_thread_id = int(path.stem)
        except ValueError:
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            config = StudioConfig(project_name=data["project_name"], config_dir=config_dir)
        except (*_CONFIG_RESOLUTION_ERRORS, KeyError, json.JSONDecodeError):
            continue

        _pending_dialogues[message_thread_id] = _DialogueState(
            kind=data["kind"], trace_id=data["trace_id"], config=config,
            system_prompt=system_prompt, transcript=data["transcript"],
        )


def _generate_run_id() -> str:
    """Même format que cli.py::_generate_run_id — dupliqué plutôt qu'importé
    de cli.py pour ne pas faire dépendre ce module (bas niveau) du point
    d'entrée CLI (haut niveau)."""
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def _validation_tool_call(state: _DialogueState, draft: str) -> tuple[str, dict[str, Any]]:
    """Outil du registre + arguments à appeler pour valider le brouillon,
    selon le type de dialogue — voir _present_draft."""
    if state.kind == "feature":
        return "valider_fiche_feature", {"run_id": state.trace_id, "content": draft}
    return "valider_fiche_projet", {"content": draft}


async def _present_draft(
    bot: Any, chat_id: int, message_thread_id: int, state: _DialogueState, draft: str,
) -> None:
    """
    Poste le brouillon de fiche produit par le PM et déclenche sa
    confirmation Oui/Non — réutilise le même mécanisme que les outils
    destructifs du registre (execute_tool + pending_confirmations +
    build_confirmation_keyboard), au lieu d'une réponse tapée comme en CLI.
    """
    tool_name, args = _validation_tool_call(state, draft)
    result = await execute_tool(tool_name, args, config=state.config)

    if result.status == "needs_confirmation":
        confirmation_id = uuid.uuid4().hex[:12]
        pending_confirmations[confirmation_id] = (tool_name, args, state.config)
        await bot.send_message(
            chat_id,
            f"{draft}\n\n{result.summary}",
            message_thread_id=message_thread_id,
            reply_markup=build_confirmation_keyboard(confirmation_id),
        )
        return

    # Ne devrait pas arriver avec la classification actuelle des outils de
    # validation (requiert_confirmation=True) — couvert pour rester correct
    # si elle change un jour.
    await bot.send_message(
        chat_id, format_tool_result(result), message_thread_id=message_thread_id,
    )


async def _start_dialogue(
    bot: Any, chat_id: int, message_thread_id: int,
    config: StudioConfig, kind: Literal["feature", "project"],
    trace_id: str, transcript_seed: str,
) -> None:
    """Cœur partagé de start_feature_dialogue/start_project_dialogue — un
    seul appel PM, puis question postée + état enregistré, ou brouillon
    présenté directement si le PM n'a besoin d'aucune question.

    "typing" envoyé avant l'appel PM (potentiellement long, Claude Code CLI)
    — sans ça, le déclenchement initial d'un dialogue (bouton menu ou
    commande) reste silencieux jusqu'à la première question, contrairement
    aux tours suivants (voir handle_dialogue_reply, qui l'envoie déjà) —
    gap trouvé en usage réel (aucun retour visible après avoir cliqué
    « Cadrer le projet », voir docs/roadmap.md)."""
    await bot.send_chat_action(chat_id, "typing", message_thread_id=message_thread_id)

    system_prompt = build_cadrage_system_prompt()
    transcript = [transcript_seed]
    tracer = RunTracer.for_run(config, trace_id).for_agent("pm", Phase.CADRAGE)

    turn, *_ = await run_pm_turn(config, tracer, system_prompt, transcript, _MODEL_KEY)

    state = _DialogueState(
        kind=kind, trace_id=trace_id, config=config,
        system_prompt=system_prompt, transcript=transcript,
    )

    if turn.kind == "question":
        transcript.append(f"PM : {turn.text}")
        _pending_dialogues[message_thread_id] = state
        _persist_dialogue_state(message_thread_id, state)
        await bot.send_message(chat_id, turn.text, message_thread_id=message_thread_id)
        return

    await _present_draft(bot, chat_id, message_thread_id, state, turn.text)


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
    await _start_dialogue(
        bot, chat_id, message_thread_id, config, "feature", run_id,
        "Objectif initial de l'utilisateur : (non précisé — nouvelle feature "
        "démarrée depuis Telegram, /new_feature).",
    )


async def start_project_dialogue(
    bot: Any, chat_id: int, message_thread_id: int, config: StudioConfig, project_name: str,
) -> None:
    """
    Démarre le dialogue de cadrage PM pour un nouveau projet (/new_project,
    ADR 0015) — même mécanisme que start_feature_dialogue, mais produit une
    fiche **projet** (`templates/card-projet.md.template`, via l'outil
    valider_fiche_projet) plutôt qu'une fiche feature. Pas de run_id : une
    fiche projet n'appartient à aucun run (voir _DialogueState.trace_id).

    Args:
        Voir start_feature_dialogue — project_name en plus, déjà connu
        (dossier/repo/topic créés avant cet appel, voir
        studio.telegram.new_project_flow).
    """
    trace_id = f"cadrage-projet-{project_name}"
    await _start_dialogue(
        bot, chat_id, message_thread_id, config, "project", trace_id,
        f"Cadrage d'un nouveau PROJET (pas une feature) : {project_name}.",
    )


def cancel_dialogue(message_thread_id: int) -> bool:
    """
    Interrompt un dialogue de cadrage en attente dans ce topic (ADR 0015,
    Décision 6, /stop, voir tools.registry._handle_stop_run) — rien à
    sauvegarder sur disque : aucune fiche n'est écrite avant la validation
    explicite de l'utilisateur (voir _present_draft).

    Returns:
        True si un dialogue était en attente et a été interrompu, False
        sinon (l'appelant traite ça comme "rien à interrompre ici").
    """
    state = _pending_dialogues.pop(message_thread_id, None)
    if state is None:
        return False
    _delete_persisted_dialogue_state(message_thread_id)
    return True


async def handle_dialogue_reply(
    bot: Any, chat_id: int, message_thread_id: Optional[int], text: str,
) -> bool:
    """
    Traite un message tapé dans un topic où un dialogue de cadrage PM est en
    attente (voir start_feature_dialogue/start_project_dialogue) — à appeler
    par telegram.handlers._on_message AVANT le dispatch slash/langage naturel
    habituel : un message dans ce contexte est la réponse de l'utilisateur
    au PM, pas une nouvelle commande (même sémantique que le input() du
    dialogue terminal — tout ce qui est tapé est la réponse).

    Args:
        message_thread_id: None si le message vient de General (aucun
            dialogue n'y est jamais enregistré — /new_feature et
            /new_project engagent tous deux leur dialogue dans un
            topic-projet, jamais en General) — retourne False immédiatement
            dans ce cas.

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

    tracer = RunTracer.for_run(state.config, state.trace_id).for_agent("pm", Phase.CADRAGE)
    turn, *_ = await run_pm_turn(
        state.config, tracer, state.system_prompt, state.transcript, _MODEL_KEY,
    )

    if turn.kind == "question":
        state.transcript.append(f"PM : {turn.text}")
        _persist_dialogue_state(message_thread_id, state)
        await bot.send_message(chat_id, turn.text, message_thread_id=message_thread_id)
        return True

    del _pending_dialogues[message_thread_id]
    _delete_persisted_dialogue_state(message_thread_id)
    await _present_draft(bot, chat_id, message_thread_id, state, turn.text)
    return True
