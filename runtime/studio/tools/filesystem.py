"""
Opérations filesystem pour devaimazing.

Lecture et écriture des fiches .md, project-map, architect-map.
Injection des skills dans les prompts.
"""

from pathlib import Path


async def read_card(card_path: Path) -> str:
    """
    Lit une fiche .md.

    Args:
        card_path: Chemin absolu vers la fiche.

    Returns:
        Contenu de la fiche en texte.

    Raises:
        FileNotFoundError: Si la fiche n'existe pas.
    """
    ...


async def write_card(card_path: Path, content: str) -> None:
    """
    Écrit ou écrase une fiche .md.

    Args:
        card_path: Chemin absolu vers la fiche.
        content: Contenu Markdown à écrire.

    Side effects:
        Crée ou écrase le fichier. Crée les répertoires parents si nécessaire.
    """
    ...


async def append_feedback(card_path: Path, agent_source: str, feedback: str) -> None:
    """
    Ajoute une entrée dans la section Feedback d'une fiche.

    Args:
        card_path: Chemin absolu vers la fiche.
        agent_source: Nom de l'agent qui donne le feedback.
        feedback: Texte du feedback.

    Side effects:
        Modifie la fiche en place.

    Raises:
        FileNotFoundError: Si la fiche n'existe pas.
        ValueError: Si la fiche ne contient pas de section Feedback.
    """
    ...


async def inject_skills(base_prompt: str, skill_names: list[str], skills_dir: Path) -> str:
    """
    Injecte les skills dans un prompt système.

    Args:
        base_prompt: Contenu de prompts/<agent>.md.
        skill_names: Liste des noms de skills à injecter (sans extension).
        skills_dir: Répertoire contenant les fichiers skill .md.

    Returns:
        Prompt enrichi avec les skills en appendice.

    Raises:
        FileNotFoundError: Si un skill n'existe pas.

    Example:
        >>> prompt = await inject_skills(
        ...     base_prompt="Tu es l'agent Backend...",
        ...     skill_names=["stub-first", "error-handling"],
        ...     skills_dir=Path("/home/user/devaimazing/skills"),
        ... )
    """
    ...
