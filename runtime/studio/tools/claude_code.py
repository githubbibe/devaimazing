"""
Wrapper subprocess pour Claude Code CLI.

Lance Claude Code en sous-process avec le prompt et le modèle spécifiés.
Capture la sortie, parse le JSON, retourne le résultat.
"""

import subprocess
from pathlib import Path
from typing import Optional


async def run_claude_code(
    prompt: str,
    model: str,
    cwd: Path,
    timeout_seconds: int = 300,
    output_format: str = "json",
) -> dict:
    """
    Lance Claude Code CLI en sous-process.

    Args:
        prompt: Prompt à envoyer à Claude Code.
        model: Identifiant du modèle (ex: claude-opus-4-8).
        cwd: Répertoire de travail (repo projet).
        timeout_seconds: Timeout en secondes avant abandon.
        output_format: Format de sortie (json recommandé pour parsing).

    Returns:
        Dictionnaire avec les champs : content, usage (tokens), duration_ms.

    Raises:
        TimeoutError: Si le sous-process dépasse timeout_seconds.
        RuntimeError: Si Claude Code retourne un code d'erreur non nul.
        ValueError: Si la sortie JSON est invalide.

    Example:
        >>> result = await run_claude_code(
        ...     prompt="Écris la fiche racine pour : ajouter un endpoint de login",
        ...     model="claude-opus-4-8",
        ...     cwd=Path("/home/user/code/aimazing/webaimazing-v2"),
        ... )
        >>> print(result["content"])
    """
    ...
