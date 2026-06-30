"""
Opérations Git pour devaimazing.

Gère les commits par agent (identité Git distincte par agent),
les branches de run, et les merges.
"""

from pathlib import Path

AGENT_GIT_IDENTITIES = {
    "pm":        ("pm-aimazing",        "pm@aimazing.fr"),
    "architect": ("architect-aimazing", "architect@aimazing.fr"),
    "back":      ("back-aimazing",      "back@aimazing.fr"),
    "front":     ("front-aimazing",     "front@aimazing.fr"),
    "test":      ("test-aimazing",      "test@aimazing.fr"),
    "security":  ("security-aimazing",  "security@aimazing.fr"),
}


async def commit_as_agent(
    repo_path: Path,
    agent: str,
    message: str,
    files: list[str],
) -> str:
    """
    Crée un commit dans le repo projet au nom d'un agent.

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
        Crée un commit dans le repo projet.

    Example:
        >>> hash = await commit_as_agent(
        ...     repo_path=Path("/home/user/code/aimazing/webaimazing-v2"),
        ...     agent="back",
        ...     message="feat: add login endpoint stub",
        ...     files=["backend/auth/endpoints.py"],
        ... )
    """
    ...
