"""
Node LangGraph - Closer.

Phase.CLOTURE (phase 10), Python pur, zéro appel LLM (voir docs/workflow.md
phase 10, docs/roadmap.md). Les commits ont déjà été réalisés au fil des
phases 4 à 9 (un commit par tâche d'agent terminée, commit_per_task dans
config/studio.yml) ; ce node ne committe plus en bloc.
"""

from datetime import datetime, timezone
from pathlib import Path

import httpx

from studio.config import StudioConfig
from studio.metrics import MetricsCollector, record_agent_result
from studio.state import AgentResult, RunStatus, StudioState
from studio.tools.filesystem import read_card, write_card
from studio.tools.git import merge_run_branch

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_MAP_TEMPLATE_PATH = _DEVAIMAZING_ROOT / "templates" / "project-map.md.template"
_PLACEHOLDER_NTFY_TOPIC = "<PLACEHOLDER_TOPIC>"


def _insert_table_rows(content: str, section_heading: str, rows: list[str]) -> str:
    """
    Insère `rows` (lignes de tableau markdown déjà formatées) juste après la
    ligne de séparation `|---|...` qui suit `section_heading`, sur le modèle
    de templates/project-map.md.template.
    """
    heading_index = content.index(section_heading)
    separator_index = content.index("\n|---", heading_index)
    line_end = content.index("\n", separator_index + 1)
    return content[:line_end] + "\n" + "\n".join(rows) + content[line_end:]


async def _update_project_map(config: StudioConfig, state: StudioState) -> None:
    """
    Met à jour project-map.md : une ligne par fichier produit dans la
    section "Carte des fichiers", une ligne de résumé dans "Historique des
    runs" (voir templates/project-map.md.template pour la structure).
    Crée le fichier depuis le template s'il n'existe pas encore.
    """
    relative_path = config.get("reference_files", {}).get("project_map", "specs/project-map.md")
    project_map_path = config.repo_path / relative_path

    if project_map_path.is_file():
        content = await read_card(project_map_path)
    else:
        content = _PROJECT_MAP_TEMPLATE_PATH.read_text(encoding="utf-8").replace(
            "{{PROJECT_NAME}}", config.project_name
        )

    output_files = sorted({f for r in state.agent_results for f in r.output_files})
    if output_files:
        file_rows = []
        for relative_file in output_files:
            agent = next(
                (r.agent for r in state.agent_results if relative_file in r.output_files), "-"
            )
            file_rows.append(f"| {relative_file} | - | {agent} | {state.run_id} | - |")
        content = _insert_table_rows(content, "## Carte des fichiers", file_rows)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    objective_summary = (state.objective_raw or "-")[:80]
    history_row = (
        f"| {state.run_id} | {date_str} | {objective_summary} | completed "
        f"| {len(output_files)} | - |"
    )
    content = _insert_table_rows(content, "## Historique des runs", [history_row])

    await write_card(project_map_path, content)


async def _notify(config: StudioConfig, message: str) -> None:
    """
    Envoie une notification ntfy. No-op si le topic est encore le
    placeholder par défaut de config/studio.yml (voir docs/roadmap.md —
    non bloquant tant que ce n'est pas remplacé par un topic réel).
    """
    ntfy_config = config.get("notifications", {}).get("ntfy", {})
    topic = ntfy_config.get("topic")
    if not topic or topic == _PLACEHOLDER_NTFY_TOPIC:
        return
    server_url = ntfy_config.get("server_url", "https://ntfy.sh")
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{server_url}/{topic}", content=message.encode("utf-8"))


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node closer.

    Args:
        state: État courant du run, avec state.current_phase=
            Phase.CLOTURE. state.branch_name doit être renseigné (créé par
            le PM en phase 3, voir studio.state.StudioState.branch_name).
            Tous les artefacts des phases précédentes (state.agent_results)
            doivent être présents.

    Returns:
        - Si le merge réussit : état final du run,
          state.status=RunStatus.COMPLETED, state.completed_at renseigné.
          Un AgentResult est ajouté à state.agent_results
          (agent="closer", phase=state.current_phase).
        - Si le merge échoue (conflit) : state.status=
          RunStatus.WAITING_HUMAN, state.requires_manual_intervention=True,
          state.intervention_reason renseigné avec le détail — le run
          reste en phase Phase.CLOTURE, pas de retry automatique (résolution
          de conflit = intervention humaine, voir tools/git.py).

    Raises:
        ValueError: Si state.branch_name n'est pas renseigné (bug amont —
            la branche aurait dû être créée et enregistrée en phase 3).

    Side effects:
        - Merge la branche du run vers git.base_branch (develop par défaut)
          via tools.git.merge_run_branch. La branche du run n'est pas
          supprimée (traçabilité et audit, voir tools/git.py).
        - Si le merge réussit : met à jour project-map.md du projet cible
          (voir _update_project_map) — nouveaux fichiers produits par le
          run, ligne d'historique.
        - Consulte MetricsCollector.get_run_summary(state.run_id) et inclut
          le nombre de tâches et le total de tokens dans la notification
          finale — best effort, un run sans tâche enregistrée n'est pas
          bloquant (ex. run minimal en test).
        - Envoie une notification ntfy ("✅ ... terminé" ou, en cas
          d'échec de merge, un message d'attente de validation) — no-op si
          le topic est toujours le placeholder par défaut.

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.CLOTURE,
        ...     branch_name="studio/ajout-panier-a3f9c",
        ...     agent_cards={"back": "specs/run-042/back.md"},
        ... )
        >>> state = await run(state)
        >>> state.status
        <RunStatus.COMPLETED: 'completed'>

    Notes:
        Ce node n'appelle jamais tools.claude_code.run_claude_code ni
        tools.ollama.run_ollama — toute la logique est déterministe (voir
        docs/roadmap.md, étape 1 : "closer en phase 10 est Python pur,
        sans appel LLM"). Sa propre activation est tout de même enregistrée
        via studio.metrics.record_agent_result (model="n/a", zéro token)
        pour que metrics.db reflète l'intégralité du run, closer compris.
    """
    config = StudioConfig.from_env()

    if state.branch_name is None:
        raise ValueError(
            "state.branch_name manquant : la branche du run doit avoir été créée et "
            "enregistrée en phase 3 (PM, studio.tools.git.create_run_branch)"
        )

    base_branch = config.get("git", {}).get("base_branch", "develop")

    try:
        await merge_run_branch(config.repo_path, state.branch_name, target_branch=base_branch)
    except RuntimeError as exc:
        await _notify(
            config,
            f"⏸ Checkpoint clôture — merge {state.branch_name} vers {base_branch} échoué, "
            f"validation requise",
        )
        return {
            "status": RunStatus.WAITING_HUMAN,
            "requires_manual_intervention": True,
            "intervention_reason": f"Merge de {state.branch_name} vers {base_branch} a échoué : {exc}",
        }

    await _update_project_map(config, state)

    metrics = MetricsCollector(config.metrics_db_path)
    try:
        summary = await metrics.get_run_summary(state.run_id)
        tokens_total = summary["tokens_prompt"] + summary["tokens_completion"]
        summary_suffix = f" ({summary['task_count']} tâches, {tokens_total} tokens)"
    except ValueError:
        # Aucune tâche enregistrée pour ce run (ex. run minimal en test) — pas bloquant.
        summary_suffix = ""

    await _notify(config, f"✅ {state.project_name} terminé{summary_suffix}")

    agent_result = AgentResult(agent="closer", phase=state.current_phase, status="success")
    await record_agent_result(config, state, agent_result, model="n/a")

    return {
        "agent_results": state.agent_results + [agent_result],
        "status": RunStatus.COMPLETED,
        "completed_at": datetime.now(timezone.utc),
    }
