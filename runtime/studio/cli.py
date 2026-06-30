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

import click
from rich.console import Console

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def main():
    """devaimazing - Studio de développement multi-agents local-first."""
    pass


@main.command()
@click.argument("project")
@click.option("--objective", "-o", help="Objectif du run (sinon demandé interactivement)")
@click.option("--dry-run", is_flag=True, help="Simule le run sans exécuter les agents")
def run(project: str, objective: str, dry_run: bool):
    """Démarre un nouveau run sur le projet PROJECT."""
    ...


@main.command()
@click.argument("run_id")
def resume(run_id: str):
    """Reprend un run en attente de validation humaine."""
    ...


@main.command()
@click.argument("project")
@click.option("--limit", "-n", default=10, help="Nombre de runs à afficher")
def runs(project: str, limit: int):
    """Liste les runs du projet PROJECT."""
    ...


@main.command()
@click.argument("run_id")
@click.option("--format", "-f", type=click.Choice(["table", "json"]), default="table")
def metrics(run_id: str, format: str):
    """Affiche les métriques du run RUN_ID."""
    ...


@main.command()
def projects():
    """Liste les projets configurés dans config/projects/."""
    ...


@main.command()
def doctor():
    """
    Vérifie que l'environnement est correctement configuré.

    Contrôles :
    - Ollama accessible et modèle présent
    - Claude Code CLI disponible et authentifié
    - SQLite initialisable (state.db et metrics.db)
    - Git configuré avec identités agents
    - Prometheus accessible (si activé)
    """
    ...
