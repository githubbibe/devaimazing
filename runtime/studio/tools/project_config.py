"""
Écriture de config/projects/<nom>.yml — création (write_project_config,
depuis un template, réutilisée par `devaimazing new-project` et par
studio.telegram.new_project_flow, ADR 0015) et édition ciblée d'un fichier
déjà existant (set_project_thread_id : ajoute ou met à jour uniquement la
clé telegram.thread_id, par édition de texte ciblée plutôt que par un
round-trip YAML complet — yaml.safe_dump perdrait tous les commentaires
existants, abondants dans ces fichiers, voir ADR 0013).
"""

import re
from pathlib import Path

import yaml

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_CONFIG_TEMPLATE_PATH = (
    _DEVAIMAZING_ROOT / "templates" / "project-config.yml.template"
)

# Ne cible que le bloc généré par cette fonction elle-même (telegram: suivi
# immédiatement de sa seule clé thread_id) — c'est le seul writer de ce bloc,
# pas la peine de gérer une structure telegram: arbitraire écrite à la main.
_TELEGRAM_BLOCK_PATTERN = re.compile(
    r"(?m)^telegram:\n[ \t]+thread_id:[ \t]*(?P<value>\S+)[ \t]*$"
)


def write_project_config(config_path: Path, name: str, repo_path: Path) -> None:
    """
    Crée config/projects/<nom>.yml depuis templates/project-config.yml.template.

    Args:
        config_path: Chemin de destination (config/projects/<nom>.yml).
        name: Nom du projet (substitué dans le template).
        repo_path: Chemin du repo cible (substitué dans le template).

    Side effects:
        Crée config_path (et son répertoire parent si nécessaire), écrase un
        fichier existant au même chemin sans avertissement — à l'appelant de
        vérifier au préalable si un écrasement doit être évité (voir
        cli.py::_new_project_async, qui court-circuite avant d'appeler cette
        fonction si le fichier existe déjà).
    """
    content = _PROJECT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
    content = content.replace("{{PROJECT_NAME}}", name).replace("{{REPO_PATH}}", str(repo_path))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")


async def set_project_thread_id(config_path: Path, thread_id: int) -> None:
    """
    Ajoute ou met à jour la clé telegram.thread_id dans un fichier projet.

    Args:
        config_path: Chemin vers config/projects/<nom>.yml (doit exister).
        thread_id: Identifiant du topic Telegram Forum associé au projet.

    Raises:
        FileNotFoundError: Si config_path n'existe pas.
        RuntimeError: Si le résultat n'est pas un YAML valide portant la
            bonne valeur — le fichier original n'est alors pas modifié.

    Side effects:
        Réécrit config_path sur disque si la validation réussit.
    """
    if not config_path.is_file():
        raise FileNotFoundError(f"Fichier projet introuvable : {config_path}")

    original = config_path.read_text(encoding="utf-8")
    match = _TELEGRAM_BLOCK_PATTERN.search(original)

    if match:
        updated = (
            original[: match.start("value")] + str(thread_id) + original[match.end("value") :]
        )
    else:
        separator = "" if original.endswith("\n") else "\n"
        block = (
            f"{separator}\n"
            "# Ajouté automatiquement lors de la création du topic Telegram\n"
            "# associé à ce projet (ADR 0013).\n"
            "telegram:\n"
            f"  thread_id: {thread_id}\n"
        )
        updated = original + block

    parsed = yaml.safe_load(updated)
    if not isinstance(parsed, dict) or parsed.get("telegram", {}).get("thread_id") != thread_id:
        raise RuntimeError(
            f"Écriture de telegram.thread_id invalide pour {config_path} — "
            "fichier non modifié."
        )

    config_path.write_text(updated, encoding="utf-8")
