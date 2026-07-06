"""
Opérations Git pour devaimazing.

Gère les commits par agent (identité Git distincte par agent), un commit à la fin
de chaque tâche terminée (pas seulement en phase 10), le nommage et la création
de branches de run, et le merge final vers develop.
"""

import hashlib
import time
from pathlib import Path

AGENT_GIT_IDENTITIES = {
    "pm":        ("pm-aimazing",        "pm@aimazing.fr"),
    "architect": ("architect-aimazing", "architect@aimazing.fr"),
    "back":      ("back-aimazing",      "back@aimazing.fr"),
    "front":     ("front-aimazing",     "front@aimazing.fr"),
    "test":      ("test-aimazing",      "test@aimazing.fr"),
    "security":  ("security-aimazing",  "security@aimazing.fr"),
}


def slugify_feature_name(feature_name: str) -> str:
    """
    Transforme le nom de feature fourni par l'utilisateur en slug utilisable
    dans un nom de branche.

    Args:
        feature_name: Nom brut fourni par l'utilisateur en phase 1.

    Returns:
        Slug en minuscules, espaces remplacés par des tirets, caractères
        spéciaux retirés.

    Example:
        >>> slugify_feature_name("Features Qui Fait Tout")
        "features-qui-fait-tout"
    """
    ...


def generate_branch_name(feature_name: str) -> str:
    """
    Génère le nom de branche complet pour un run.

    Format : studio/<slug-feature>-<hash5>
    Le hash est calculé sur (timestamp + nom de la feature) pour garantir
    l'unicité même en cas de noms de feature identiques ou proches.

    Args:
        feature_name: Nom brut fourni par l'utilisateur en phase 1.

    Returns:
        Nom de branche complet, ex: "studio/features-qui-fait-tout-a3f9c".

    Example:
        >>> generate_branch_name("features qui fait tout")
        "studio/features-qui-fait-tout-a3f9c"
    """
    ...


async def create_run_branch(repo_path: Path, feature_name: str, base_branch: str = "develop") -> str:
    """
    Crée la branche du run à partir de base_branch.

    Appelée au démarrage effectif du run (fin de phase 3, fiches dépendantes
    écrites), jamais pendant le dialogue de cadrage de la phase 1.

    Args:
        repo_path: Chemin absolu vers le repo projet.
        feature_name: Nom de la feature tel que fourni par l'utilisateur.
        base_branch: Branche de base à partir de laquelle créer la branche du run.

    Returns:
        Nom de la branche créée.

    Raises:
        RuntimeError: Si la création de branche échoue (conflit, permissions).

    Side effects:
        Crée une branche Git dans le repo projet.
    """
    ...


async def commit_as_agent(
    repo_path: Path,
    agent: str,
    message: str,
    files: list[str],
) -> str:
    """
    Crée un commit dans le repo projet au nom d'un agent, à la fin de sa tâche.

    Appelé après chaque tâche d'agent terminée (phases 4 à 9), pas uniquement
    en phase 10. Chaque commit constitue un point de restauration en cas
    d'échec ou de renvoi ultérieur dans le run.

    Args:
        repo_path: Chemin absolu vers le repo projet.
        agent: Nom de l'agent (doit être dans AGENT_GIT_IDENTITIES).
        message: Message de commit (format conventional commits).
        files: Liste des fichiers à inclure dans le commit (chemins relatifs au repo).

    Returns:
        Hash du commit créé.

    Raises:
        ValueError: Si agent inconnu ou files vide.
        RuntimeError: Si le commit Git échoue.

    Side effects:
        Crée un commit dans le repo projet, sur la branche du run courant.

    Example:
        >>> hash = await commit_as_agent(
        ...     repo_path=Path("/home/user/code/aimazing/webaimazing-v2"),
        ...     agent="back",
        ...     message="feat: add login endpoint stub",
        ...     files=["backend/auth/endpoints.py"],
        ... )
    """
    ...


async def merge_run_branch(repo_path: Path, branch_name: str, target_branch: str = "develop") -> str:
    """
    Merge la branche du run vers la branche cible, en phase 10 après validation finale.

    Args:
        repo_path: Chemin absolu vers le repo projet.
        branch_name: Nom de la branche du run à merger.
        target_branch: Branche cible (develop par défaut).

    Returns:
        Hash du commit de merge.

    Raises:
        RuntimeError: Si le merge échoue (conflit).

    Side effects:
        Merge la branche dans target_branch. Ne supprime pas la branche du run
        (conservée pour traçabilité et audit).
    """
    ...
