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
n'importe quel message reçu dans le topic pendant que le run est en attente
déclenche la reprise (voir handle_run_reply) — le contenu du texte n'est
pas lu, aucun vrai mécanisme de feedback/rejet à ce niveau (cohérent avec
/reject, explicitement différé par l'ADR 0015).
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from studio.config import StudioConfig
from studio.graph import build_graph
from studio.state import Phase, RunStatus, StudioState
from studio.telegram import menu
from studio.tools import planification, queries
from studio.tools.git import commit_safety_snapshot, current_branch, push_branch
from studio.tools.ollama import ExternalServiceError

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
    await bot.edit_message_text(
        _progress_text(feature_name, state), chat_id=chat_id, message_id=message_id,
        reply_markup=reply_markup,
    )


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
                f"La fiche de {feature_name!r} a été modifiée depuis son dernier run "
                "(déjà terminé) — ce cas n'est pas géré automatiquement en v1, il "
                "faudrait relancer une fiche via /new_feature.",
                message_thread_id=message_thread_id,
            )
        return {}

    if status == RunStatus.FAILED:
        summary = queries.build_progression_summary(run_state)
        await bot.send_message(
            chat_id,
            f"Le run de {feature_name!r} a échoué : {summary['intervention_reason']}. "
            "Pas de reprise automatique.",
            message_thread_id=message_thread_id,
        )
        return {}

    # WAITING_HUMAN (mémoire process perdue, ex. redémarrage du bot) ou
    # IN_PROGRESS (process tué avant tout checkpoint, comme devaimazing retry)
    # — dans les deux cas on reprend, la différence est needs_state_update.
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
            needs_state_update=(status == RunStatus.WAITING_HUMAN),
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
            sent.message_id, initial_state=initial_state, needs_state_update=False,
        )
    )


async def _execute_run(
    bot: Any, chat_id: int, message_thread_id: int, config: StudioConfig, run_id: str,
    feature_name: str, message_id: int, *,
    initial_state: Optional[StudioState], needs_state_update: bool,
) -> None:
    graph = await build_graph(config)
    try:
        thread_config = _thread_config(run_id)
        if needs_state_update:
            await graph.aupdate_state(
                thread_config,
                {"status": RunStatus.IN_PROGRESS, "awaiting_human_validation": False},
            )

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
    # FAILED : planification.md reste "en cours" (projection best-effort du
    # statut réel, le détail — intervention_reason — reste dans le message
    # Telegram déjà édité ci-dessus, pas de reprise automatique v1).

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


async def handle_run_reply(
    bot: Any, chat_id: int, message_thread_id: Optional[int], text: str,
) -> bool:
    """
    Traite un message tapé dans un topic où un run est en attente de
    validation humaine (voir _execute_run, status == WAITING_HUMAN) — à
    appeler par telegram.handlers._on_message, après handle_dialogue_reply.

    Le contenu de `text` n'est jamais lu (voir docstring module) —
    n'importe quel message reprend le run.

    Returns:
        True si un run était en attente pour ce topic et que ce message a
        été consommé comme déclencheur de reprise. False sinon (l'appelant
        continue son dispatch normal).
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
            active.message_id, initial_state=None, needs_state_update=True,
        )
    )
    return True
