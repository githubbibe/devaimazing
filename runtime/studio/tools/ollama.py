"""
Wrapper pour l'API Ollama locale.

Appelle le modèle local via l'API Ollama avec le prompt système et le prompt utilisateur.
Gère les retries et les timeouts.
"""

import asyncio
import time
from typing import Optional

import httpx
from ollama import AsyncClient, RequestError, ResponseError

# Alignés sur la section `ollama` de config/studio.yml (max_retries: 3) et sur le
# pattern de backoff exponentiel de skills/retry-patterns.md.
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 1.0

# Codes HTTP qu'on ne retente jamais (voir skills/retry-patterns.md,
# section "Ce qu'on ne retente PAS") : requête invalide, auth, modèle inconnu.
NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404}


class ExternalServiceError(Exception):
    """Erreur d'un service externe (voir skills/error-handling.md)."""


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
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        started_at = time.monotonic()
        try:
            async with AsyncClient(host=base_url, timeout=timeout_seconds) as client:
                response = await client.chat(model=model, messages=messages, stream=False)
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                f"Ollama n'a pas répondu dans le délai imparti ({timeout_seconds}s) "
                f"pour le modèle {model!r}"
            ) from exc
        except ResponseError as exc:
            if exc.status_code in NON_RETRYABLE_STATUS_CODES:
                raise ExternalServiceError(
                    f"Ollama a rejeté la requête (code {exc.status_code}) : {exc.error}"
                ) from exc
            last_error = exc
        except (ConnectionError, RequestError) as exc:
            last_error = exc
        else:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            return {
                "content": response.message.content or "",
                "tokens_prompt": response.prompt_eval_count or 0,
                "tokens_completion": response.eval_count or 0,
                "duration_ms": duration_ms,
            }

        if attempt < MAX_ATTEMPTS - 1:
            await asyncio.sleep(BASE_DELAY_SECONDS * (2 ** attempt))

    raise ExternalServiceError(
        f"Ollama injoignable après {MAX_ATTEMPTS} tentatives (modèle {model!r}, {base_url})"
    ) from last_error
