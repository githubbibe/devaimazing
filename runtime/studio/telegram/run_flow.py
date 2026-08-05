"""
Orchestration de `/run <nom_feature>` (ADR 0015, Décision 4) — lance ou
reprend le pipeline d'une feature déjà cadrée (fiche validée via
/new_feature, studio.telegram.pm_dialogue) si sa fiche a changé depuis son
dernier run (spécs/planification.md, studio.tools.planification).

Reproduit le pattern build_graph/aget_state/ainvoke/finally-close déjà
utilisé par studio.cli (_run_async/_resume_async/_retry_async), mais avec
graph.astream(stream_mode="values") pour afficher l'avancement en direct
(édition d'un seul message Telegram, rate-limitée à 1/s) — usage nouveau de
l'API LangGraph dans ce dépôt (ainvoke seul était utilisé jusqu'ici),
l'API est native de CompiledStateGraph.

Exécution en tâche de fond (asyncio.create_task) plutôt qu'attendue dans le
handler : le pipeline peut prendre plusieurs minutes (plusieurs appels LLM
séquentiels), et le bot doit rester réactif aux autres messages pendant ce
temps — voir _active_runs, qui sert à la fois de garde anti-double-
lancement et de référence forte (une tâche asyncio non référencée peut être
garbage-collectée avant sa fin).

Checkpoint humain en cours de run (audit Architecte/Sécu, agent bloqué) :
un message dédié est posté (pas une édition, voir _send_checkpoint_notification)
avec le fichier produit en pièce jointe et un bouton « ✅ Continuer » — soit ce
bouton, soit n'importe quel texte tapé dans le topic (voir handle_run_reply)
déclenche la reprise. Le contenu du texte n'est jamais lu, aucun vrai
mécanisme de feedback/rejet à ce niveau (cohérent avec /reject,
explicitement différé par l'ADR 0015 — révisé pour la notification/l'action
de reprise elle-même, voir ADR 0015 révision 2026-08-05).
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from studio.config import StudioConfig, project_context
from studio.graph import build_graph
from studio.state import Phase, RunStatus, StudioState
from studio.telegram import menu
from studio.tools import planification, queries
from studio.tools.git import commit_safety_snapshot, current_branch, push_branch
from studio.tools.ollama import ExternalServiceError

# Callback du bouton « ✅ Continuer » posté par _send_checkpoint_notification
# — une seule valeur constante (pas de préfixe + id à parser) : la reprise ne
# porte aucun paramètre, elle s'appuie sur callback.message.message_thread_id
# (voir handlers.build_router) pour retrouver le run actif dans _active_runs,
# exactement comme handle_run_reply le fait déjà pour un texte tapé.
CHECKPOINT_CONTINUE_CALLBACK = "run_checkpoint_continue"

# Callback du bouton « 🔄 Réessayer » posté par _send_failure_notification —
# préfixe + nom de feature (pas juste message_thread_id comme
# CHECKPOINT_CONTINUE_CALLBACK) : contrairement à un checkpoint WAITING_HUMAN
# tout juste posté par CE process, un run FAILED peut être rouvert bien plus
# tard (après un redémarrage du bot, _active_runs vide) — le handler doit
# pouvoir retrouver la feature sans dépendre d'un état en mémoire encore présent.
RETRY_FAILED_CALLBACK_PREFIX = "run_retry_failed"

# Mêmes types que cli.py::_EXTERNAL_SERVICE_ERRORS — déjà porteurs d'un
# message clair côté outil (Ollama, Claude Code CLI, Git), affichés
# proprement au lieu de laisser remonter la traceback brute.
_EXTERNAL_SERVICE_ERRORS = (TimeoutError, ExternalServiceError, RuntimeError)

_PROGRESS_EDIT_INTERVAL_SECONDS = 1.0


@dataclass
class _RunState:
    """
    État en mémoire process d'un run actif dans un topic — même famille que
    studio.telegram.pm_dialogue._pending_dialogues et
    studio.telegram.new_project_flow._pending_project_name (jamais
    persisté, perdu si le bot redémarre en cours de run).
    """

    config: StudioConfig
    run_id: str
    feature_name: str
    chat_id: int
    message_id: int
    task: Optional[Any] = None
    awaiting_human: bool = False


# Clé = message_thread_id (un topic = un run actif au plus, cohérent avec le
# reste des états en attente de ce module).
_active_runs: dict[int, _RunState] = {}


def _specs_dir(config: StudioConfig) -> str:
    return config.get("structure", {}).get("specs_dir", "specs/")


def _card_root_relative(config: StudioConfig, run_id: str) -> str:
    return str(Path(_specs_dir(config)) / run_id / "card-root.md")


def _thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _read_card_root(config: StudioConfig, run_id: str) -> str:
    return (config.repo_path / _card_root_relative(config, run_id)).read_text(encoding="utf-8")


def _progress_text(feature_name: str, state: dict[str, Any]) -> str:
    summary = queries.build_progression_summary(state)
    lines = [
        f"Run {feature_name!r} — phase {summary['current_phase']} — statut {summary['status']}",
        f"Agent courant : {summary['current_agent'] or '—'}",
    ]
    if summary["last_result"]:
        last_result = summary["last_result"]
        lines.append(
            f"Dernier résultat : {last_result['agent']} — {last_result['status']} "
            f"(itération {last_result['iteration']})"
        )
    if summary["requires_manual_intervention"]:
        lines.append(f"Intervention manuelle requise : {summary['intervention_reason']}")
    return "\n".join(lines)


async def _edit_progress(
    bot: Any, chat_id: int, message_id: int, feature_name: str, state: dict[str, Any],
    *, reply_markup: Any = None,
) -> None:
    """
    Édite le message de progression — absorbe l'erreur Telegram "message is
    not modified" (même motif que handlers._safe_edit_text) : le flush final
    obligatoire de _execute_run répète parfois exactement le texte/clavier de
    la dernière édition périodique (rien de nouveau à afficher entre les
    deux, ex. une seule activation d'agent entre deux éditions rate-limitées)
    — sans ce garde-fou, TelegramBadRequest n'était pas rattrapé par
    _EXTERNAL_SERVICE_ERRORS et tuait la tâche de fond en silence, AVANT
    d'atteindre _send_checkpoint_notification pour un run WAITING_HUMAN (bug
    réel trouvé en run, todolist3, voir docs/roadmap.md).
    """
    try:
        await bot.edit_message_text(
            _progress_text(feature_name, state), chat_id=chat_id, message_id=message_id,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


def _checkpoint_continue_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Continuer", callback_data=CHECKPOINT_CONTINUE_CALLBACK),
    ]])


def _last_output_file(state: dict[str, Any]) -> Optional[str]:
    agent_results = state.get("agent_results") or []
    if not agent_results:
        return None
    output_files = agent_results[-1].output_files
    return output_files[-1] if output_files else None


async def _send_checkpoint_notification(
    bot: Any, chat_id: int, message_thread_id: int, config: StudioConfig,
    feature_name: str, state: dict[str, Any],
) -> None:
    """
    Poste un NOUVEAU message (pas une édition, contrairement au reste de la
    progression, voir _edit_progress) quand le run atteint un checkpoint
    humain (status == WAITING_HUMAN) — gap constaté en usage réel
    (todolist3, run gestion-taches) : une édition n'émet aucune notification
    push côté Telegram, invisible tant que l'utilisateur n'ouvre pas le chat
    de lui-même ; le texte de progression seul ne dit pas non plus quoi
    relire ni où (voir ADR 0015, révision 2026-08-05).

    Le dernier fichier produit (state.agent_results[-1].output_files, ex.
    architect-brief.md) est joint en pièce jointe s'il existe sur disque —
    même pattern que studio.telegram.pm_dialogue._present_draft (pièce
    jointe, pas de limite à 4096 caractères, lecture d'un seul tenant).

    Le bouton « ✅ Continuer » est posté sur un message TEXTE séparé de la
    pièce jointe (jamais en légende du document) : edit_message_text (utilisé
    par le callback, voir handlers.py) ne peut éditer qu'un message texte,
    jamais un message document — même contrainte déjà documentée pour
    _present_draft.
    """
    summary = queries.build_progression_summary(state)
    lines = [
        f"⏸ Run {feature_name!r} en pause — validation requise avant de continuer "
        f"(phase {summary['current_phase']}).",
    ]
    if summary["last_result"]:
        last_result = summary["last_result"]
        lines.append(f"Dernier résultat : {last_result['agent']} — {last_result['status']}.")
    text = "\n".join(lines)

    output_file = _last_output_file(state)
    artifact_path = config.repo_path / output_file if output_file else None
    if artifact_path is not None and artifact_path.is_file():
        document = BufferedInputFile(artifact_path.read_bytes(), filename=artifact_path.name)
        await bot.send_document(
            chat_id, document, caption=text, message_thread_id=message_thread_id,
        )
        await bot.send_message(
            chat_id, "Continuer ?", message_thread_id=message_thread_id,
            reply_markup=_checkpoint_continue_keyboard(),
        )
    else:
        await bot.send_message(
            chat_id, text, message_thread_id=message_thread_id,
            reply_markup=_checkpoint_continue_keyboard(),
        )


def _retry_failed_keyboard(feature_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🔄 Réessayer", callback_data=f"{RETRY_FAILED_CALLBACK_PREFIX}:{feature_name}",
        ),
    ]])


def _reset_failed_state_for_retry(state: dict[str, Any]) -> dict[str, Any]:
    """
    Construit les updates (aupdate_state) qui redonnent un budget
    d'itérations neuf à l'agent qui a fait échouer le run — à appeler
    uniquement après que la cause du blocage a été corrigée (ex. changement
    de modèle agents_local, requirements.txt manquant réparé à la main).

    routing.agent_iteration_count compte les entrées de state.agent_results
    dont agent ET phase correspondent à la tentative courante — sans purger
    les entrées de la dernière activation en échec, une reprise se
    reheurterait immédiatement à max_iterations_exceeded, avant même un
    nouvel appel LLM (le run redeviendrait FAILED sur-le-champ).

    state.agent_results[-1] identifie l'agent/phase à réinitialiser — c'est
    la dernière tentative enregistrée juste avant que max_iterations_exceeded
    (studio.nodes.backend/frontend/security/test/pm, seul chemin qui produit
    RunStatus.FAILED dans ce studio) ne coupe court sans ajouter sa propre
    entrée. Générique aux cinq nodes qui peuvent échouer ainsi, y compris pm
    (phase FICHES) qui n'utilise pas state.agent_sequence/current_agent_index
    contrairement aux quatre autres.

    Purge aussi state.retry_scope[agent] s'il existe : sans ça, la
    prochaine activation reste en « mode correction ciblée »
    (backend.py::_targeted_correction_prompt) sur le fichier qui a fait
    échouer le run, avec un message d'erreur devenu obsolète si ce fichier a
    été corrigé à la main entre-temps (gap trouvé en run réel, todolist3,
    backend/routers/tasks.py) — mieux vaut une régénération complète propre
    qu'un modèle confronté à un contenu déjà correct mais présenté comme
    fautif.
    """
    agent_results = state.get("agent_results") or []
    if not agent_results:
        kept_results = agent_results
        failed_agent = None
    else:
        last = agent_results[-1]
        kept_results = [
            r for r in agent_results if not (r.agent == last.agent and r.phase == last.phase)
        ]
        failed_agent = last.agent

    retry_scope = state.get("retry_scope") or {}
    kept_retry_scope = (
        {k: v for k, v in retry_scope.items() if k != failed_agent}
        if failed_agent is not None else retry_scope
    )

    return {
        "status": RunStatus.IN_PROGRESS,
        "requires_manual_intervention": False,
        "intervention_reason": None,
        "agent_results": kept_results,
        "retry_scope": kept_retry_scope,
    }


async def _send_failure_notification(
    bot: Any, chat_id: int, message_thread_id: int, feature_name: str, state: dict[str, Any],
) -> None:
    """
    Poste un NOUVEAU message (même principe que _send_checkpoint_notification)
    quand un run atteint RunStatus.FAILED (max_iterations_exceeded) — avec le
    bouton « 🔄 Réessayer » (voir retry_failed_run, ADR 0015 révision
    2026-08-05 bis) plutôt que de se contenter de rapporter l'échec sans
    action possible (gap constaté en usage réel, todolist3, run
    gestion-taches : agent back en échec à cause d'un modèle local trop
    faible — après correction de la config, aucune reprise n'était possible
    sans relancer tout le cadrage/audit amont/fiches via /modifier_feature).
    """
    summary = queries.build_progression_summary(state)
    text = (
        f"❌ Run {feature_name!r} en échec (phase {summary['current_phase']}).\n"
        f"{summary['intervention_reason']}\n\n"
        "Si la cause a été corrigée (ex. changement de modèle), \"Réessayer\" "
        "redonne un budget d'itérations neuf à l'agent concerné et reprend le run."
    )
    await bot.send_message(
        chat_id, text, message_thread_id=message_thread_id,
        reply_markup=_retry_failed_keyboard(feature_name),
    )


async def retry_failed_run(
    bot: Any, chat_id: int, message_thread_id: int, config: StudioConfig, feature_name: str,
) -> dict[str, Any]:
    """
    Réessaie un run FAILED (bouton « 🔄 Réessayer », voir
    _send_failure_notification) — à la différence de cli.py::retry (réservé
    aux runs IN_PROGRESS interrompus par un crash), ce chemin est réservé aux
    runs FAILED et redonne explicitement un budget d'itérations neuf à
    l'agent qui a échoué (voir _reset_failed_state_for_retry) avant de
    reprendre, exactement comme une reprise WAITING_HUMAN pour le reste.

    Returns:
        {"error": ...} si la feature est inconnue ; sinon {} après avoir
        soit répondu directement dans le topic (rien à réessayer, run déjà
        en cours), soit lancé la tâche de fond.
    """
    entry = await planification.find_entry(config, feature_name)
    if entry is None:
        return {
            "error": f"Feature {feature_name!r} inconnue dans planification.md.",
        }

    active = _active_runs.get(message_thread_id)
    if active is not None and active.task is not None and not active.task.done():
        await bot.send_message(
            chat_id, f"Un run est déjà en cours pour {feature_name!r} dans ce topic.",
            message_thread_id=message_thread_id,
        )
        return {}

    run_state = await queries.fetch_run_state(config, entry.run_id)
    if run_state is None or run_state.get("status") != RunStatus.FAILED:
        await bot.send_message(
            chat_id, f"Rien à réessayer pour {feature_name!r} (pas de run en échec).",
            message_thread_id=message_thread_id,
        )
        return {}

    sent = await bot.send_message(
        chat_id, f"Nouvel essai pour {feature_name!r}...", message_thread_id=message_thread_id,
    )
    run_entry = _RunState(
        config=config, run_id=entry.run_id, feature_name=feature_name,
        chat_id=chat_id, message_id=sent.message_id,
    )
    _active_runs[message_thread_id] = run_entry
    run_entry.task = asyncio.create_task(
        _execute_run(
            bot, chat_id, message_thread_id, config, entry.run_id, feature_name,
            sent.message_id, initial_state=None,
            state_update=_reset_failed_state_for_retry(run_state),
        )
    )
    return {}


async def start_run(
    bot: Any, chat_id: int, message_thread_id: int, config: StudioConfig, feature_name: str,
) -> dict[str, Any]:
    """
    Point d'entrée de /run <nom_feature> — rapide (I/O locale seulement),
    spawn la suite en tâche de fond sans l'attendre (voir docstring module).

    Returns:
        {"error": ...} si la feature est inconnue (l'appelant, voir
        tools.registry._handle_run_feature, traduit en ValueError) ; sinon
        {} après avoir soit répondu directement dans le topic (rien à
        faire, échec déjà connu, run déjà en cours), soit lancé la tâche de
        fond.
    """
    entry = await planification.find_entry(config, feature_name)
    if entry is None:
        return {
            "error": (
                f"Feature {feature_name!r} inconnue dans planification.md — "
                "validez d'abord sa fiche avec /new_feature."
            )
        }

    active = _active_runs.get(message_thread_id)
    if active is not None and active.run_id == entry.run_id:
        if active.awaiting_human:
            await bot.send_message(
                chat_id,
                f"Le run de {feature_name!r} est en attente de validation humaine — "
                "réponds dans ce topic pour continuer.",
                message_thread_id=message_thread_id,
            )
            return {}
        if active.task is not None and not active.task.done():
            await bot.send_message(
                chat_id, f"Le run de {feature_name!r} est déjà en cours.",
                message_thread_id=message_thread_id,
            )
            return {}
    elif active is not None and active.task is not None and not active.task.done():
        await bot.send_message(
            chat_id,
            f"Un autre run ({active.feature_name!r}) est déjà en cours dans ce topic.",
            message_thread_id=message_thread_id,
        )
        return {}

    hash_now = planification.hash_content(_read_card_root(config, entry.run_id))
    run_state = await queries.fetch_run_state(config, entry.run_id)

    if run_state is None:
        await _launch_fresh(bot, chat_id, message_thread_id, config, entry, feature_name, hash_now)
        return {}

    status = run_state.get("status")

    if status == RunStatus.COMPLETED:
        if hash_now == entry.content_hash:
            await bot.send_message(
                chat_id, f"Rien à implémenter pour {feature_name!r}, tout a déjà été fait.",
                message_thread_id=message_thread_id,
            )
        else:
            await bot.send_message(
                chat_id,
                f"La fiche de {feature_name!r} a été modifiée sur disque depuis son "
                "dernier run (déjà terminé) sans passer par le cadrage — utilise "
                "/modifier_feature pour repartir d'un dialogue PM cohérent plutôt que "
                "d'éditer la fiche directement.",
                message_thread_id=message_thread_id,
            )
        return {}

    if status == RunStatus.FAILED:
        await _send_failure_notification(bot, chat_id, message_thread_id, feature_name, run_state)
        return {}

    # WAITING_HUMAN (mémoire process perdue, ex. redémarrage du bot) ou
    # IN_PROGRESS (process tué avant tout checkpoint, comme devaimazing retry)
    # — dans les deux cas on reprend, la différence est state_update.
    sent = await bot.send_message(
        chat_id, f"Reprise du run de {feature_name!r}...", message_thread_id=message_thread_id,
    )
    run_entry = _RunState(
        config=config, run_id=entry.run_id, feature_name=feature_name,
        chat_id=chat_id, message_id=sent.message_id,
    )
    _active_runs[message_thread_id] = run_entry
    run_entry.task = asyncio.create_task(
        _execute_run(
            bot, chat_id, message_thread_id, config, entry.run_id, feature_name,
            sent.message_id, initial_state=None,
            state_update=(
                {"status": RunStatus.IN_PROGRESS, "awaiting_human_validation": False}
                if status == RunStatus.WAITING_HUMAN else None
            ),
        )
    )
    return {}


async def _launch_fresh(
    bot: Any, chat_id: int, message_thread_id: int, config: StudioConfig,
    entry: "planification.PlanificationEntry", feature_name: str, content_hash: str,
) -> None:
    await planification.upsert_entry(
        config,
        planification.PlanificationEntry(
            feature_name=feature_name, statut="en cours",
            run_id=entry.run_id, content_hash=content_hash,
        ),
    )
    sent = await bot.send_message(
        chat_id, f"Lancement de {feature_name!r}...", message_thread_id=message_thread_id,
    )
    initial_state = StudioState(
        run_id=entry.run_id,
        project_name=config.project_name,
        current_phase=Phase.CADRAGE,
        status=RunStatus.IN_PROGRESS,
        card_root_path=_card_root_relative(config, entry.run_id),
        started_at=datetime.now(timezone.utc),
    )
    run_entry = _RunState(
        config=config, run_id=entry.run_id, feature_name=feature_name,
        chat_id=chat_id, message_id=sent.message_id,
    )
    _active_runs[message_thread_id] = run_entry
    run_entry.task = asyncio.create_task(
        _execute_run(
            bot, chat_id, message_thread_id, config, entry.run_id, feature_name,
            sent.message_id, initial_state=initial_state,
        )
    )


async def _execute_run(
    bot: Any, chat_id: int, message_thread_id: int, config: StudioConfig, run_id: str,
    feature_name: str, message_id: int, *,
    initial_state: Optional[StudioState], state_update: Optional[dict[str, Any]] = None,
) -> None:
    graph = await build_graph(config)
    try:
        thread_config = _thread_config(run_id)
        # project_context (ContextVar, pas os.environ) : les nodes du graphe
        # appellent StudioConfig.from_env() en interne (voir leurs
        # docstrings) — sans ce bloc, ils ne verraient aucun projet courant
        # (le chemin Telegram, contrairement au CLI, ne positionne jamais
        # DEVAIMAZING_PROJECT) et lèveraient ValueError dès le premier node,
        # plantant la tâche de fond en silence (trouvé en run réel, aucune
        # trace/checkpoint après le premier node, voir docs/roadmap.md). Un
        # ContextVar plutôt qu'os.environ car ce process bot peut exécuter
        # plusieurs runs de projets différents en même temps (un topic
        # chacun) — os.environ serait partagé et racy entre leurs `await`.
        with project_context(config.project_name, config.config_dir):
            if state_update:
                await graph.aupdate_state(thread_config, state_update)

            last_edit_ts = 0.0
            final_state: dict[str, Any] = {}
            async for state in graph.astream(
                initial_state, config=thread_config, stream_mode="values",
            ):
                final_state = state
                now = time.monotonic()
                if now - last_edit_ts >= _PROGRESS_EDIT_INTERVAL_SECONDS:
                    await _edit_progress(bot, chat_id, message_id, feature_name, final_state)
                    last_edit_ts = now
        # Flush final obligatoire : le dernier état doit toujours être
        # affiché, même si moins d'1s s'est écoulée depuis la dernière
        # édition (voir ADR 0015, Décision 4 — regroupement des éditions).
        # Clavier racine du menu (ADR 0015, Décision 7) rattaché seulement
        # sur un statut terminal (COMPLETED/FAILED) — pas WAITING_HUMAN,
        # encore « en cours » (le topic attend une réponse pour reprendre,
        # pas un nouveau choix de menu).
        final_status = final_state.get("status")
        menu_keyboard = (
            menu.build_root_keyboard(in_topic=True)
            if final_status in (RunStatus.COMPLETED, RunStatus.FAILED) else None
        )
        await _edit_progress(
            bot, chat_id, message_id, feature_name, final_state, reply_markup=menu_keyboard,
        )
    except _EXTERNAL_SERVICE_ERRORS as exc:
        await bot.edit_message_text(
            f"Run de {feature_name!r} interrompu : {exc}", chat_id=chat_id, message_id=message_id,
        )
        _active_runs.pop(message_thread_id, None)
        return
    finally:
        # build_graph() laisse la connexion SQLite du checkpointer ouverte
        # par conception (voir sa docstring) — même fermeture explicite que
        # cli.py::_run_async.
        await graph.checkpointer.conn.close()

    status = final_state.get("status")

    if status == RunStatus.WAITING_HUMAN:
        active = _active_runs.get(message_thread_id)
        if active is not None:
            active.awaiting_human = True
        await _send_checkpoint_notification(
            bot, chat_id, message_thread_id, config, feature_name, final_state,
        )
        return

    if status == RunStatus.COMPLETED:
        card_root_path = final_state.get("card_root_path") or _card_root_relative(config, run_id)
        content = (config.repo_path / card_root_path).read_text(encoding="utf-8")
        await planification.upsert_entry(
            config,
            planification.PlanificationEntry(
                feature_name=feature_name, statut="fait",
                run_id=run_id, content_hash=planification.hash_content(content),
                merged_commit=final_state.get("merged_commit"),
            ),
        )

    if status == RunStatus.FAILED:
        # planification.md reste "en cours" (projection best-effort du
        # statut réel, aucune colonne dédiée) — le détail (intervention_reason)
        # est dans _send_failure_notification, avec le bouton "Réessayer"
        # (ADR 0015, révision 2026-08-05 bis).
        await _send_failure_notification(bot, chat_id, message_thread_id, feature_name, final_state)

    _active_runs.pop(message_thread_id, None)


async def stop_active_run(message_thread_id: int) -> Optional[dict[str, Any]]:
    """
    Arrête immédiatement le run actif dans ce topic (ADR 0015, Décision 6,
    /stop, voir tools.registry._handle_stop_run) — annule la tâche de fond
    en cours (asyncio.Task.cancel(), la connexion checkpointer se ferme
    quand même dans le `finally` de _execute_run) puis sauvegarde (commit +
    push) le repo cible sous l'identité système devaimazing-bot, même
    helper que archive_projet (tools.git.commit_safety_snapshot).

    Le prochain /run sur cette feature reprendra depuis le dernier
    checkpoint LangGraph, comme après un crash (voir start_run — aucun
    statut "arrêté" n'existe dans RunStatus, planification.md n'est pas
    touché ici).

    Returns:
        None si aucun run n'est actif dans ce topic. Sinon
        {"feature_name", "run_id", "commit"} — commit est None si le repo
        n'avait rien à sauvegarder.
    """
    active = _active_runs.pop(message_thread_id, None)
    if active is None:
        return None

    if active.task is not None and not active.task.done():
        active.task.cancel()

    commit_hash = await commit_safety_snapshot(
        active.config.repo_path,
        message="chore: sauvegarde avant arrêt du run (Devaimazing, /stop)",
    )
    if commit_hash is not None:
        branch = await current_branch(active.config.repo_path)
        await push_branch(active.config.repo_path, branch)

    return {"feature_name": active.feature_name, "run_id": active.run_id, "commit": commit_hash}


async def _resume_waiting_run(
    bot: Any, chat_id: int, message_thread_id: Optional[int],
) -> bool:
    """
    Cœur partagé de handle_run_reply (texte tapé) et handle_checkpoint_continue
    (bouton « ✅ Continuer », voir _send_checkpoint_notification) — reprend le
    run en attente de validation humaine dans ce topic, si présent.

    Returns:
        True si un run était en attente pour ce topic et a été relancé.
        False sinon (l'appelant continue son dispatch normal).
    """
    if message_thread_id is None:
        return False

    active = _active_runs.get(message_thread_id)
    if active is None or not active.awaiting_human:
        return False

    active.awaiting_human = False
    active.task = asyncio.create_task(
        _execute_run(
            bot, chat_id, message_thread_id, active.config, active.run_id, active.feature_name,
            active.message_id, initial_state=None,
            state_update={"status": RunStatus.IN_PROGRESS, "awaiting_human_validation": False},
        )
    )
    return True


async def handle_run_reply(
    bot: Any, chat_id: int, message_thread_id: Optional[int], text: str,
) -> bool:
    """
    Traite un message tapé dans un topic où un run est en attente de
    validation humaine (voir _execute_run, status == WAITING_HUMAN) — à
    appeler par telegram.handlers._on_message, après handle_dialogue_reply.

    Le contenu de `text` n'est jamais lu (voir docstring module) —
    n'importe quel message reprend le run, voir _resume_waiting_run.
    """
    return await _resume_waiting_run(bot, chat_id, message_thread_id)


async def handle_checkpoint_continue_callback(
    bot: Any, chat_id: int, message_thread_id: Optional[int],
) -> bool:
    """
    Traite un clic sur le bouton « ✅ Continuer » posté par
    _send_checkpoint_notification — à appeler par telegram.handlers, sur le
    callback_query CHECKPOINT_CONTINUE_CALLBACK. Même reprise que
    handle_run_reply, voir _resume_waiting_run.
    """
    return await _resume_waiting_run(bot, chat_id, message_thread_id)
