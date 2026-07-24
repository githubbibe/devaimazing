"""
Requêtes de lecture seule sur l'état d'un run ou d'un projet.

Extrait et généralise une logique déjà écrite dans studio.cli (diagnostic de
run, parsing du tableau d'historique, liste des projets) pour qu'elle soit
consommable à la fois par le CLI existant et par le futur registre d'outils
(studio.tools.registry, ADR 0013) — pas de duplication entre les deux.
Aucune de ces fonctions n'écrit quoi que ce soit ni n'appelle de service
externe payant.
"""

from pathlib import Path
from typing import Any, Optional

from studio.config import StudioConfig
from studio.graph import build_graph


async def _fetch_run_state(config: StudioConfig, run_id: str) -> Optional[dict[str, Any]]:
    """
    Récupère l'état brut d'un run depuis le checkpointer SQLite.

    Args:
        config: Configuration du projet auquel appartient le run.
        run_id: Identifiant du run.

    Returns:
        Le dict d'état (StudioState sérialisé) si le run existe, None sinon.

    Side effects:
        Construit puis ferme une connexion au checkpointer (voir le
        commentaire équivalent dans studio.cli._run_async : sans cette
        fermeture explicite, le process ne se termine jamais).
    """
    graph = await build_graph(config)
    try:
        snapshot = await graph.aget_state({"configurable": {"thread_id": run_id}})
        return snapshot.values or None
    finally:
        await graph.checkpointer.conn.close()


def _summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    agent_sequence = state.get("agent_sequence") or []
    current_agent_index = state.get("current_agent_index", 0)
    current_agent = (
        agent_sequence[current_agent_index]
        if 0 <= current_agent_index < len(agent_sequence)
        else None
    )
    return {
        "current_phase": str(state.get("current_phase")),
        "status": str(state.get("status")),
        "current_agent": current_agent,
    }


async def get_run_snapshot(config: StudioConfig, run_id: str) -> dict[str, Any]:
    """
    Statut actuel d'un run (phase, statut, agent courant).

    Args:
        config: Configuration du projet auquel appartient le run.
        run_id: Identifiant du run.

    Returns:
        {"found": False} si le run n'existe pas, sinon {"found": True,
        "current_phase", "status", "current_agent"}.
    """
    state = await _fetch_run_state(config, run_id)
    if state is None:
        return {"found": False}
    return {"found": True, **_summarize_state(state)}


def build_progression_summary(state: dict[str, Any]) -> dict[str, Any]:
    """
    Diagnostic détaillé à partir d'un état de run déjà récupéré (phase,
    statut, agent courant, dernier résultat, intervention manuelle
    éventuelle) — logique reprise de studio.cli._print_retry_diagnostic,
    exposée ici en dict structuré pur pour être réutilisée à la fois par
    get_run_progression (qui récupère l'état lui-même) et par le CLI
    existant (qui a déjà l'état en main, pour éviter un double fetch).

    Args:
        state: Dict d'état d'un run (StudioState sérialisé, non None).

    Returns:
        {"current_phase", "status", "current_agent", "last_result" (dict
        agent/status/iteration ou None), "requires_manual_intervention",
        "intervention_reason"}.
    """
    agent_results = state.get("agent_results") or []
    last_result = agent_results[-1] if agent_results else None

    return {
        **_summarize_state(state),
        "last_result": (
            {
                "agent": last_result.agent,
                "status": last_result.status,
                "iteration": last_result.iteration,
            }
            if last_result is not None
            else None
        ),
        "requires_manual_intervention": bool(state.get("requires_manual_intervention", False)),
        "intervention_reason": state.get("intervention_reason"),
    }


async def get_run_progression(config: StudioConfig, run_id: str) -> dict[str, Any]:
    """
    Diagnostic détaillé d'un run (voir build_progression_summary), en
    récupérant lui-même l'état depuis le checkpointer.

    Args:
        config: Configuration du projet auquel appartient le run.
        run_id: Identifiant du run.

    Returns:
        {"found": False} si le run n'existe pas, sinon {"found": True, ...}
        avec le contenu de build_progression_summary(state) fusionné.
    """
    state = await _fetch_run_state(config, run_id)
    if state is None:
        return {"found": False}
    return {"found": True, **build_progression_summary(state)}


def parse_run_history_table(content: str) -> list[list[str]]:
    """
    Parse les lignes de données de la section "Historique des runs" d'un
    project-map.md (voir studio.nodes.closer._insert_table_rows — même
    structure de tableau, lue en sens inverse ici).

    Déplacée telle quelle depuis studio.cli._parse_run_history_table.
    """
    heading_index = content.find("## Historique des runs")
    if heading_index == -1:
        return []
    separator_index = content.find("\n|---", heading_index)
    if separator_index == -1:
        return []
    next_heading_index = content.find("\n## ", separator_index)
    section_end = next_heading_index if next_heading_index != -1 else len(content)
    section = content[separator_index:section_end]

    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        is_separator_row = all(set(cell) <= {"-"} for cell in cells)
        if is_separator_row or not any(cells):
            continue
        rows.append(cells)
    return rows


async def list_projects(config_dir: Path) -> list[str]:
    """
    Liste les projets configurés dans config_dir/projects/.

    Args:
        config_dir: Répertoire de config (voir StudioConfig.config_dir).

    Returns:
        Noms de projet (nom de fichier sans extension), triés, liste vide
        si le répertoire n'existe pas ou est vide.
    """
    projects_dir = config_dir / "projects"
    if not projects_dir.is_dir():
        return []
    return sorted(p.stem for p in projects_dir.glob("*.yml"))
