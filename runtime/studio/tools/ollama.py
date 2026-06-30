"""
Wrapper pour l'API Ollama locale.

Appelle le modèle local via l'API Ollama avec le prompt système et le prompt utilisateur.
Gère les retries et les timeouts.
"""

from pathlib import Path


async def run_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str,
    base_url: str = "http://localhost:11434",
    timeout_seconds: int = 120,
) -> dict:
    """
    Appelle le modèle Ollama local.

    Args:
        system_prompt: Prompt système de l'agent (contenu de prompts/<agent>.md).
        user_prompt: Prompt utilisateur (contenu de la fiche + skills injectés).
        model: Identifiant du modèle Ollama (ex: qwen2.5:7b-instruct).
        base_url: URL de l'API Ollama.
        timeout_seconds: Timeout en secondes.

    Returns:
        Dictionnaire avec : content (texte généré), tokens_prompt, tokens_completion, duration_ms.

    Raises:
        ExternalServiceError: Si Ollama ne répond pas ou retourne une erreur.
        TimeoutError: Si la génération dépasse timeout_seconds.

    Side effects:
        Aucun. Ne modifie pas de fichiers.

    Example:
        >>> result = await run_ollama(
        ...     system_prompt="Tu es l'agent Backend...",
        ...     user_prompt="Voici ta fiche : ...",
        ...     model="qwen2.5:7b-instruct",
        ... )
    """
    ...
