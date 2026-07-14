"""
CLI devaimazing - Point d'entrée principal.

Usage:
    devaimazing run <project>           Démarre un run
    devaimazing resume <run-id>         Reprend après checkpoint humain
    devaimazing retry <run-id>          Rejoue un run interrompu en cours de nœud (crash)
    devaimazing run-agent <project> <run-id> <agent>
                                         Exécute un seul agent hors run complet (test isolé)
    devaimazing runs <project>          Liste les runs d'un projet
    devaimazing metrics <run-id>        Affiche les métriques d'un run
    devaimazing new-project <name>      Initialise un nouveau projet cible (dossier
                                         frère, repo Git, config/projects/<name>.yml)
    devaimazing projects                Liste les projets configurés
    devaimazing doctor                  Vérifie l'environnement
"""

import asyncio
import difflib
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
from studio.nodes import architect, backend, closer, frontend, pm, security
from studio.nodes import test as test_node
from studio.routing import AGENT_TO_NODE
from studio.state import Phase, RunStatus, StudioState
from studio.tools.git import (
    checkout_branch,
    create_github_remote,
    create_initial_commit,
    init_repo,
    push_branch,
)

console = Console()


def _devaimazing_root() -> Path:
    """Racine du repo studio (parent de runtime/studio/cli.py)."""
    return Path(__file__).resolve().parents[2]


def _resolve_config_dir() -> Optional[Path]:
    """
    Répertoire de config, override DEVAIMAZING_CONFIG_DIR pris en compte
    (comme StudioConfig.from_env() — mais le nom de projet vient ici de
    l'argument CLI, pas de DEVAIMAZING_PROJECT).
    """
    config_dir_raw = os.environ.get("DEVAIMAZING_CONFIG_DIR")
    return Path(config_dir_raw).expanduser() if config_dir_raw else None


def _config_projects_dir() -> Path:
    base_config_dir = _resolve_config_dir() or _devaimazing_root() / "config"
    return base_config_dir / "projects"


def _gh_available() -> bool:
    return shutil.which("gh") is not None


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

    base_branch = config.get("git", {}).get("base_branch", "develop")
    await checkout_branch(config.repo_path, base_branch)

    graph = await build_graph(config)
    try:
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
    finally:
        # build_graph() laisse la connexion SQLite du checkpointer ouverte
        # par conception (voir sa docstring) : sans cette fermeture, le
        # process ne se termine jamais (Py_Finalize attend indéfiniment le
        # thread worker aiosqlite) — trouvé lors du premier run réel
        # (2026-07-10), voir docs/roadmap.md.
        await graph.checkpointer.conn.close()


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
    try:
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
    finally:
        # Voir le commentaire équivalent dans _run_async.
        await graph.checkpointer.conn.close()


def _print_retry_diagnostic(run_id: str, state: dict) -> None:
    """
    Diagnostic affiché avant de rejouer un run planté — champs déjà
    présents dans StudioState, aucun ajout de champ (pas d'horodatage,
    décision actée dans docs/roadmap.md).
    """
    agent_sequence = state.get("agent_sequence") or []
    current_agent_index = state.get("current_agent_index", 0)
    if 0 <= current_agent_index < len(agent_sequence):
        current_agent = agent_sequence[current_agent_index]
    else:
        current_agent = "inconnu"

    console.print(f"[bold]Diagnostic — run {run_id}[/bold]")
    console.print(f"  Phase courante : {state.get('current_phase')}")
    console.print(f"  Agent courant : {current_agent}")
    console.print(f"  Statut : {state.get('status')}")

    agent_results = state.get("agent_results") or []
    if agent_results:
        last_result = agent_results[-1]
        console.print(
            f"  Dernier résultat : {last_result.agent} — {last_result.status} "
            f"(itération {last_result.iteration})"
        )

    if state.get("requires_manual_intervention"):
        console.print(
            f"  [red]Intervention manuelle requise : {state.get('intervention_reason')}[/red]"
        )


@main.command()
@click.argument("run_id")
@click.option("--project", required=True, help="Projet du run (nécessaire pour charger sa config)")
def retry(run_id: str, project: str):
    """Rejoue un run interrompu en cours de nœud (crash), hors attente de validation humaine."""
    asyncio.run(_retry_async(run_id, project))


async def _retry_async(run_id: str, project: str) -> None:
    _export_project_env(project)
    config = _load_config(project)
    graph = await build_graph(config)
    try:
        thread_config = _thread_config(run_id)

        snapshot = await graph.aget_state(thread_config)
        if not snapshot.values:
            console.print(f"[red]Run {run_id} introuvable pour le projet {project}.[/red]")
            return

        state = snapshot.values
        status = state.get("status")
        if state.get("awaiting_human_validation") or status == RunStatus.WAITING_HUMAN:
            console.print(
                f"[yellow]Run {run_id} en attente de validation humaine — "
                f"utiliser devaimazing resume à la place.[/yellow]"
            )
            return
        if status != RunStatus.IN_PROGRESS:
            console.print(f"[yellow]Run {run_id} au statut {status}, rien à rejouer.[/yellow]")
            return

        _print_retry_diagnostic(run_id, state)

        if not click.confirm("Rejouer ce run ?", default=False):
            console.print("[yellow]Retry annulé.[/yellow]")
            return

        final_state = await graph.ainvoke(None, config=thread_config)
        _print_run_outcome(run_id, final_state)
    finally:
        # Voir le commentaire équivalent dans _run_async.
        await graph.checkpointer.conn.close()


_NODE_MODULES = {
    "pm": pm,
    "architect": architect,
    "backend": backend,
    "frontend": frontend,
    "test": test_node,
    "security": security,
    "closer": closer,
}

# Rôles indexés via state.agent_sequence (voir studio.routing.AGENT_TO_NODE) —
# exclut pm/architect, dont le node ne lit jamais son propre rôle depuis
# agent_sequence (voir leurs docstrings respectives).
_PRODUCER_ROLES = [role for role in AGENT_TO_NODE if role not in ("pm", "architect")]

_RUN_AGENT_CHOICES = sorted(set(AGENT_TO_NODE) | {"closer"})


def _node_for_agent(agent: str):
    return _NODE_MODULES[AGENT_TO_NODE.get(agent, agent)]


def _specs_dir(config: StudioConfig) -> str:
    return config.get("structure", {}).get("specs_dir", "specs/")


def _discover_agent_cards(repo_path: Path, specs_run_dir_relative: Path) -> dict[str, str]:
    """
    Fiches déjà présentes sur disque pour ce run (convention <role>.md dans
    specs/<specs_dir>/<run-id>/, voir studio.nodes.pm._run_fiches) —
    reconstitue state.agent_cards sans passer par le checkpoint LangGraph
    (voir run_agent, qui ne touche jamais state.db).
    """
    cards = {}
    for role in _PRODUCER_ROLES:
        candidate = specs_run_dir_relative / f"{role}.md"
        if (repo_path / candidate).is_file():
            cards[role] = str(candidate)
    return cards


def _discover_optional(repo_path: Path, relative: Path) -> Optional[str]:
    return str(relative) if (repo_path / relative).is_file() else None


@main.command(name="run-agent")
@click.argument("project")
@click.argument("run_id")
@click.argument("agent", type=click.Choice(_RUN_AGENT_CHOICES))
@click.option(
    "--phase", "phase_name", required=True, type=click.Choice([p.name for p in Phase]),
    help="Phase à simuler — détermine le comportement du node, ne peut pas être déduite "
    "automatiquement (ex. back en Phase.STUBS vs Phase.IMPLEMENTATION).",
)
@click.option("--objective", help="Objectif du run (agent pm, phases RECEPTION/CADRAGE)")
@click.option(
    "--card", "card_override",
    help="Chemin de la fiche de AGENT (relatif au repo cible) — remplace la découverte "
    "automatique à specs/<run-id>/<agent>.md",
)
@click.option(
    "--card-root", "card_root_override",
    help="Chemin de card-root.md (relatif au repo cible), sinon déduit de "
    "specs/<run-id>/card-root.md s'il existe",
)
@click.option(
    "--architect-brief", "architect_brief_override",
    help="Chemin de architect-brief.md (relatif au repo cible), sinon déduit de "
    "specs/<run-id>/architect-brief.md s'il existe",
)
@click.option(
    "--existing-file", "existing_files", multiple=True,
    help="Chemin (relatif au repo cible) d'un fichier existant à fournir en contexte à "
    "AGENT (back/front/test uniquement) — répétable. Ne peut pas être déduit "
    "automatiquement : cette donnée (agent_card_metadata) provient du structured_output "
    "du PM en phase 3 et n'est pas persistée sur disque en dehors du checkpoint.",
)
@click.option("--branch-name", help="Nom de la branche du run (agent closer uniquement)")
@click.option(
    "--reference-dir", "reference_dir",
    help="Répertoire de fiches/fichiers de référence (structure miroir du repo cible, ex. "
    "<reference-dir>/specs/<run-id>/back.md) — si fourni, chaque fichier produit par AGENT "
    "à cette invocation (AgentResult.output_files) est comparé en diff texte exact au "
    "fichier de même chemin relatif sous ce répertoire. Sert à distinguer un agent qui lit "
    "mal une bonne fiche d'entrée d'un agent qui produit une mauvaise fiche/sortie : on "
    "compare la fiche produite à celle qu'un run de référence avait produite pour l'agent "
    "suivant.",
)
def run_agent(
    project: str,
    run_id: str,
    agent: str,
    phase_name: str,
    objective: Optional[str],
    card_override: Optional[str],
    card_root_override: Optional[str],
    architect_brief_override: Optional[str],
    existing_files: tuple,
    branch_name: Optional[str],
    reference_dir: Optional[str],
):
    """
    Exécute un seul agent hors du contexte d'un run complet (test isolé).

    Construit un StudioState minimal à partir des artefacts déjà présents
    sur disque (specs/<run-id>/) et appelle le node de AGENT directement,
    sans passer par build_graph/LangGraph — ne lit ni n'écrit jamais
    state.db (contrairement à run/resume/retry). AGENT lit et écrit pour de
    vrai dans le repo cible (fichiers, commits git), exactement comme lors
    d'un run normal — voir docs/roadmap.md, chantier "Commande CLI par
    agent" (décision du 2026-07-14).
    """
    asyncio.run(_run_agent_async(
        project, run_id, agent, phase_name, objective, card_override,
        card_root_override, architect_brief_override, existing_files, branch_name,
        reference_dir,
    ))


async def _run_agent_async(
    project: str,
    run_id: str,
    agent: str,
    phase_name: str,
    objective: Optional[str],
    card_override: Optional[str],
    card_root_override: Optional[str],
    architect_brief_override: Optional[str],
    existing_files: tuple,
    branch_name: Optional[str],
    reference_dir: Optional[str],
) -> None:
    _export_project_env(project)
    config = _load_config(project)
    phase = Phase[phase_name]

    if agent == "pm" and phase in (Phase.RECEPTION, Phase.CADRAGE) and not objective:
        objective = click.prompt("Objectif du run")

    specs_run_dir_relative = Path(_specs_dir(config)) / run_id
    agent_cards = _discover_agent_cards(config.repo_path, specs_run_dir_relative)
    if card_override:
        agent_cards[agent] = card_override

    agent_sequence = [role for role in _PRODUCER_ROLES if role in agent_cards]
    if agent in _PRODUCER_ROLES and agent not in agent_sequence:
        agent_sequence.append(agent)
    current_agent_index = agent_sequence.index(agent) if agent in agent_sequence else 0

    agent_card_metadata = {
        role: {"existing_files_to_read": list(existing_files) if role == agent else []}
        for role in set(agent_cards) | {agent}
    }

    card_root_path = card_root_override or _discover_optional(
        config.repo_path, specs_run_dir_relative / "card-root.md"
    )
    architect_brief_path = architect_brief_override or _discover_optional(
        config.repo_path, specs_run_dir_relative / "architect-brief.md"
    )

    state = StudioState(
        run_id=run_id,
        project_name=project,
        objective_raw=objective or "",
        current_phase=phase,
        status=RunStatus.IN_PROGRESS,
        agent_sequence=agent_sequence,
        current_agent_index=current_agent_index,
        card_root_path=card_root_path,
        architect_brief_path=architect_brief_path,
        agent_cards=agent_cards,
        agent_card_metadata=agent_card_metadata,
        branch_name=branch_name,
    )

    console.print(
        f"[bold]run-agent[/bold] — projet {project}, run {run_id}, agent {agent!r}, "
        f"phase {phase.name} (state.db non touché)"
    )

    node = _node_for_agent(agent)
    try:
        updates = await node.run(state)
    except (RuntimeError, KeyError, FileNotFoundError, TimeoutError, ValueError, TypeError) as exc:
        console.print(f"[red]{type(exc).__name__} : {exc}[/red]")
        return

    console.print(f"[green]Résultat — agent {agent!r} :[/green]")
    for key, value in updates.items():
        console.print(f"  {key} = {value!r}")

    if reference_dir:
        _compare_to_reference(config.repo_path, Path(reference_dir), updates)


def _compare_to_reference(repo_path: Path, reference_dir: Path, updates: dict) -> None:
    """
    Diff texte exact entre chaque fichier produit par l'invocation (voir
    AgentResult.output_files du dernier élément de updates["agent_results"])
    et le fichier de même chemin relatif sous reference_dir.

    Premier jet volontairement simple (décision utilisateur, 2026-07-14) :
    diff texte exact, pas de comparaison sémantique/structurelle — les
    reformulations libres d'un même agent d'un appel à l'autre (variance
    documentée dans docs/roadmap.md) feront donc apparaître des diffs sur du
    contenu par ailleurs correct. Assumé pour ce premier jet.
    """
    agent_results = updates.get("agent_results")
    output_files = agent_results[-1].output_files if agent_results else []

    if not output_files:
        console.print(
            "[yellow]Aucun fichier produit par cette invocation (voir "
            "AgentResult.output_files) — rien à comparer à reference-dir.[/yellow]"
        )
        return

    for relative_path in output_files:
        produced_path = repo_path / relative_path
        reference_path = reference_dir / relative_path

        if not reference_path.is_file():
            console.print(f"[yellow]? référence absente : {relative_path}[/yellow]")
            continue
        if not produced_path.is_file():
            console.print(f"[red]✗ fichier produit introuvable : {relative_path}[/red]")
            continue

        produced = produced_path.read_text(encoding="utf-8")
        reference = reference_path.read_text(encoding="utf-8")

        if produced == reference:
            console.print(f"[green]✓ identique à la référence : {relative_path}[/green]")
            continue

        console.print(f"[red]✗ diffère de la référence : {relative_path}[/red]")
        diff = difflib.unified_diff(
            reference.splitlines(keepends=True),
            produced.splitlines(keepends=True),
            fromfile=f"référence/{relative_path}",
            tofile=f"produit/{relative_path}",
        )
        console.print("".join(diff) or "(fichiers vides tous les deux)")


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


@main.command(name="new-project")
@click.argument("name")
@click.option(
    "--private/--public", default=True,
    help="Visibilité du repo GitHub créé (privé par défaut)",
)
@click.option(
    "--skip-github", is_flag=True,
    help="Ne crée pas de repo GitHub distant (repo Git local uniquement)",
)
def new_project(name: str, private: bool, skip_github: bool):
    """
    Initialise un nouveau projet cible NAME : dossier frère de devaimazing
    (repo Git séparé), et config/projects/NAME.yml dans le repo studio.
    """
    asyncio.run(_new_project_async(name, private, skip_github))


def _write_project_config(config_path: Path, name: str, repo_path: Path) -> None:
    template_path = _devaimazing_root() / "templates" / "project-config.yml.template"
    content = template_path.read_text(encoding="utf-8")
    content = content.replace("{{PROJECT_NAME}}", name).replace("{{REPO_PATH}}", str(repo_path))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")


async def _new_project_async(name: str, private: bool, skip_github: bool) -> None:
    config_path = _config_projects_dir() / f"{name}.yml"
    if config_path.exists():
        console.print(
            f"[yellow]config/projects/{name}.yml existe déjà — "
            "projet déjà initialisé, rien à faire.[/yellow]"
        )
        return

    target = _devaimazing_root().parent / name

    if target.exists():
        if not target.is_dir():
            console.print(f"[red]{target} existe déjà mais n'est pas un dossier.[/red]")
            return
        if not (target / ".git").is_dir():
            console.print(
                f"[red]{target} existe déjà mais n'est pas un repo Git — "
                "à résoudre manuellement.[/red]"
            )
            return
        console.print(f"[cyan]{target} existe déjà, réutilisation (pas de git init/GitHub).[/cyan]")
    else:
        target.mkdir(parents=True)
        await init_repo(target, initial_branch="develop")
        await create_initial_commit(target, name)
        console.print(f"[green]Repo Git initialisé dans {target}[/green]")

        if skip_github:
            console.print("[yellow]--skip-github : pas de repo GitHub distant créé.[/yellow]")
        elif not _gh_available():
            console.print(
                "[yellow]gh introuvable dans PATH — repo GitHub non créé, "
                "à faire manuellement si besoin.[/yellow]"
            )
        else:
            visibility_label = "privé" if private else "public"
            confirm_message = (
                f"Créer le repo GitHub '{name}' ({visibility_label}) "
                "et y pousser la branche develop ?"
            )
            if click.confirm(confirm_message, default=False):
                await create_github_remote(target, name, private=private)
                await push_branch(target, "develop")
                console.print("[green]Repo GitHub créé et branche develop poussée.[/green]")
            else:
                console.print(
                    "[yellow]Repo GitHub non créé — à faire manuellement si besoin.[/yellow]"
                )

    _write_project_config(config_path, name, target)
    console.print(f"[green]Config créée : {config_path}[/green]")
    console.print(f"Prochaine étape : devaimazing run {name}")


@main.command()
def projects():
    """Liste les projets configurés dans config/projects/."""
    config_dir = _config_projects_dir()
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
