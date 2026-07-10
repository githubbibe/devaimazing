"""
CLI devaimazing - Point d'entrée principal.

Usage:
    devaimazing run <project>           Démarre un run
    devaimazing resume <run-id>         Reprend après checkpoint humain
    devaimazing runs <project>          Liste les runs d'un projet
    devaimazing metrics <run-id>        Affiche les métriques d'un run
    devaimazing projects                Liste les projets configurés
    devaimazing doctor                  Vérifie l'environnement
"""

import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
import httpx
from rich.console import Console
from rich.table import Table

from studio.config import StudioConfig
from studio.graph import build_graph
from studio.metrics import MetricsCollector
from studio.state import Phase, RunStatus, StudioState

console = Console()


def _resolve_config_dir() -> Optional[Path]:
    """
    Répertoire de config, override DEVAIMAZING_CONFIG_DIR pris en compte
    (comme StudioConfig.from_env() — mais le nom de projet vient ici de
    l'argument CLI, pas de DEVAIMAZING_PROJECT).
    """
    config_dir_raw = os.environ.get("DEVAIMAZING_CONFIG_DIR")
    return Path(config_dir_raw).expanduser() if config_dir_raw else None


def _load_config(project: str) -> StudioConfig:
    return StudioConfig(project_name=project, config_dir=_resolve_config_dir())


def _export_project_env(project: str) -> None:
    """
    Propage `project` (et DEVAIMAZING_CONFIG_DIR s'il est déjà résolu) dans
    l'environnement du process.

    Nécessaire avant toute invocation du graphe (build_graph + ainvoke) :
    chaque node appelle StudioConfig.from_env() en interne (voir leurs
    docstrings — la config n'est pas transmise via StudioState), et
    from_env() lit DEVAIMAZING_PROJECT depuis os.environ. _load_config()
    seul ne suffit pas : il construit une StudioConfig pour l'usage de la
    commande CLI elle-même, mais ne modifie pas l'environnement que les
    nodes liront plus tard pendant l'exécution du graphe.
    """
    os.environ["DEVAIMAZING_PROJECT"] = project
    config_dir = _resolve_config_dir()
    if config_dir is not None:
        os.environ["DEVAIMAZING_CONFIG_DIR"] = str(config_dir)


def _generate_run_id() -> str:
    """
    Identifiant de run basé sur l'horodatage (pas de compteur séquentiel
    partagé à maintenir) : run-YYYYMMDD-HHMMSS.
    """
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def _thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _print_run_outcome(run_id: str, state: dict) -> None:
    status = state.get("status")
    if status == RunStatus.WAITING_HUMAN:
        console.print(
            f"[yellow]⏸ Run {run_id} en attente de validation[/yellow] "
            f"(phase {state.get('current_phase')}). Reprendre avec : "
            f"devaimazing resume {run_id} --project <projet>"
        )
    elif status == RunStatus.COMPLETED:
        console.print(f"[green]✅ Run {run_id} terminé[/green]")
    elif status == RunStatus.FAILED:
        console.print(f"[red]❌ Run {run_id} échoué : {state.get('intervention_reason')}[/red]")
    else:
        console.print(f"Run {run_id} — statut {status}")


@click.group()
@click.version_option(version="0.1.0")
def main():
    """devaimazing - Studio de développement multi-agents local-first."""
    pass


@main.command()
@click.argument("project")
@click.option("--objective", "-o", help="Objectif du run (sinon demandé interactivement)")
@click.option("--dry-run", is_flag=True, help="Simule le run sans exécuter les agents")
def run(project: str, objective: Optional[str], dry_run: bool):
    """Démarre un nouveau run sur le projet PROJECT."""
    asyncio.run(_run_async(project, objective, dry_run))


async def _run_async(project: str, objective: Optional[str], dry_run: bool) -> None:
    _export_project_env(project)
    config = _load_config(project)
    if not objective:
        objective = click.prompt("Objectif du run")

    run_id = _generate_run_id()
    console.print(f"[bold]Run {run_id}[/bold] — projet {project}")

    if dry_run:
        console.print(f"[yellow]Dry-run[/yellow] : objectif = {objective!r}, aucun agent exécuté.")
        return

    graph = await build_graph(config)
    initial_state = StudioState(
        run_id=run_id,
        project_name=project,
        objective_raw=objective,
        current_phase=Phase.RECEPTION,
        status=RunStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
    )
    final_state = await graph.ainvoke(initial_state, config=_thread_config(run_id))
    _print_run_outcome(run_id, final_state)


@main.command()
@click.argument("run_id")
@click.option("--project", required=True, help="Projet du run (nécessaire pour charger sa config)")
def resume(run_id: str, project: str):
    """Reprend un run en attente de validation humaine."""
    asyncio.run(_resume_async(run_id, project))


async def _resume_async(run_id: str, project: str) -> None:
    _export_project_env(project)
    config = _load_config(project)
    graph = await build_graph(config)
    thread_config = _thread_config(run_id)

    snapshot = await graph.aget_state(thread_config)
    if not snapshot.values:
        console.print(f"[red]Run {run_id} introuvable pour le projet {project}.[/red]")
        return
    if not snapshot.values.get("awaiting_human_validation"):
        console.print(f"[yellow]Run {run_id} n'est pas en attente de validation.[/yellow]")
        return

    await graph.aupdate_state(
        thread_config,
        {"status": RunStatus.IN_PROGRESS, "awaiting_human_validation": False},
    )
    final_state = await graph.ainvoke(None, config=thread_config)
    _print_run_outcome(run_id, final_state)


def _parse_run_history_table(content: str) -> list[list[str]]:
    """
    Parse les lignes de données de la section "Historique des runs" d'un
    project-map.md (voir studio.nodes.closer._insert_table_rows — même
    structure de tableau, lue en sens inverse ici).
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


@main.command()
@click.argument("project")
@click.option("--limit", "-n", default=10, help="Nombre de runs à afficher")
def runs(project: str, limit: int):
    """Liste les runs du projet PROJECT."""
    asyncio.run(_runs_async(project, limit))


async def _runs_async(project: str, limit: int) -> None:
    config = _load_config(project)
    relative_path = config.get("reference_files", {}).get("project_map", "specs/project-map.md")
    project_map_path = config.repo_path / relative_path
    if not project_map_path.is_file():
        console.print(f"[yellow]Aucun project-map.md pour {project} (aucun run terminé).[/yellow]")
        return

    content = project_map_path.read_text(encoding="utf-8")
    rows = _parse_run_history_table(content)[-limit:]
    if not rows:
        console.print(f"[yellow]Aucun run enregistré pour {project}.[/yellow]")
        return

    table = Table(title=f"Runs — {project}")
    for column in ("Run ID", "Date", "Objectif", "Statut", "Fichiers créés", "Fichiers modifiés"):
        table.add_column(column)
    for row_cells in rows:
        table.add_row(*row_cells)
    console.print(table)


@main.command()
@click.argument("run_id")
@click.option("--project", required=True, help="Projet du run")
@click.option(
    "--format", "-f", "output_format", type=click.Choice(["table", "json"]), default="table"
)
def metrics(run_id: str, project: str, output_format: str):
    """Affiche les métriques du run RUN_ID."""
    asyncio.run(_metrics_async(run_id, project, output_format))


async def _metrics_async(run_id: str, project: str, output_format: str) -> None:
    config = _load_config(project)
    collector = MetricsCollector(config.metrics_db_path)
    try:
        summary = await collector.get_run_summary(run_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    if output_format == "json":
        console.print_json(json.dumps(summary, default=str))
        return

    table = Table(title=f"Métriques — {run_id}")
    table.add_column("Métrique")
    table.add_column("Valeur")
    table.add_row("Tâches", str(summary["task_count"]))
    table.add_row("Tokens prompt", str(summary["tokens_prompt"]))
    table.add_row("Tokens completion", str(summary["tokens_completion"]))
    table.add_row("Durée totale (ms)", str(summary["total_duration_ms"]))
    console.print(table)


@main.command()
def projects():
    """Liste les projets configurés dans config/projects/."""
    base_config_dir = _resolve_config_dir() or Path(__file__).resolve().parents[2] / "config"
    config_dir = base_config_dir / "projects"
    if not config_dir.is_dir():
        console.print("[yellow]Aucun répertoire config/projects/ trouvé.[/yellow]")
        return
    names = sorted(p.stem for p in config_dir.glob("*.yml"))
    if not names:
        console.print("[yellow]Aucun projet configuré.[/yellow]")
        return
    for name in names:
        console.print(f"- {name}")


async def _check_ollama_reachable(base_url: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base_url}/api/tags")
            response.raise_for_status()
        return True, base_url
    except httpx.HTTPError as exc:
        return False, f"{base_url} injoignable ({exc})"


@main.command()
@click.option(
    "--project", help="Projet à utiliser pour les vérifications SQLite/Prometheus (optionnel)"
)
def doctor(project: Optional[str]):
    """
    Vérifie que l'environnement est correctement configuré.

    Contrôles :
    - Ollama accessible et modèle présent
    - Claude Code CLI disponible et authentifié
    - SQLite initialisable (state.db et metrics.db)
    - Git configuré avec identités agents
    - Prometheus accessible (si activé)
    """
    asyncio.run(_doctor_async(project))


async def _doctor_async(project: Optional[str]) -> None:
    checks: list[tuple[str, bool, str]] = []

    claude_path = shutil.which("claude")
    checks.append(("Claude Code CLI", claude_path is not None, claude_path or "introuvable dans PATH"))

    git_path = shutil.which("git")
    checks.append(("Git", git_path is not None, git_path or "introuvable dans PATH"))

    ollama_base_url = "http://localhost:11434"
    config = None
    if project:
        try:
            config = _load_config(project)
            ollama_base_url = config.ollama_base_url
        except (FileNotFoundError, ValueError) as exc:
            checks.append((f"Config projet {project!r}", False, str(exc)))

    ollama_ok, ollama_detail = await _check_ollama_reachable(ollama_base_url)
    checks.append(("Ollama", ollama_ok, ollama_detail))

    if config is not None:
        try:
            config.state_db_path.parent.mkdir(parents=True, exist_ok=True)
            checks.append(("state.db (répertoire)", True, str(config.state_db_path)))
        except OSError as exc:
            checks.append(("state.db (répertoire)", False, str(exc)))
        try:
            MetricsCollector(config.metrics_db_path)
            checks.append(("metrics.db", True, str(config.metrics_db_path)))
        except OSError as exc:
            checks.append(("metrics.db", False, str(exc)))
    else:
        checks.append((
            "state.db / metrics.db", False,
            "non vérifié — passer --project pour ce diagnostic",
        ))

    for name, ok, detail in checks:
        symbol = "[green]OK[/green]" if ok else "[red]KO[/red]"
        console.print(f"{symbol} {name} — {detail}")
