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


# Schéma de sortie structurée pour les agents producteurs de fichiers (Back,
# Front, Test — voir prompts/backend.md, prompts/frontend.md, prompts/test.md).
# Remplace le contrat par délimiteurs texte <<<DEVAIMAZING_FILE>>> (voir
# docs/roadmap.md, chantier "sortie structurée", 2026-07-11) : passé à Ollama
# via `format`, il contraint la génération par grammaire (grammar-constrained
# decoding) — la sortie est syntaxiquement conforme par construction, plus de
# délimiteur libre à respecter. `blocked_reason` est l'échappatoire structurée
# pour le cas où l'agent détecte une impossibilité (remplace le comportement
# "explique en texte libre au lieu de produire un bloc de fichier").
FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        "blocked_reason": {"type": "string"},
    },
    "required": ["files", "blocked_reason"],
}


async def run_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str,
    base_url: str = "http://localhost:11434",
    timeout_seconds: int = 120,
    response_format: Optional[dict] = None,
) -> dict:
    """
    Appelle le modèle Ollama local.

    Args:
        system_prompt: Prompt système de l'agent (contenu de prompts/<agent>.md).
        user_prompt: Prompt utilisateur (contenu de la fiche + skills injectés).
        model: Identifiant du modèle Ollama (ex: qwen2.5:7b-instruct).
        base_url: URL de l'API Ollama.
        timeout_seconds: Timeout en secondes.
        response_format: Schéma JSON optionnel (voir FILE_OUTPUT_SCHEMA) pour
            contraindre la sortie par grammaire (grammar-constrained decoding,
            Ollama ≥0.5). Si fourni, `content` dans le retour est du texte
            JSON conforme au schéma, à parser (voir
            tools.filesystem.parse_structured_file_output). Si `None` (défaut),
            sortie texte libre inchangée.

    Returns:
        Dictionnaire avec : content (texte généré, JSON si response_format
        fourni), tokens_prompt, tokens_completion, duration_ms.

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
        ...     response_format=FILE_OUTPUT_SCHEMA,
        ... )

    Notes:
        `format` n'est utile que pour un modèle qui le supporte réellement
        (Qwen 2.5 le supporte pour le structured output — pas pour le
        function-calling sur schémas complexes, voir issue
        ollama/ollama#7051, docs/roadmap.md). Un modèle ou une version
        d'Ollama qui l'ignore silencieusement retomberait sur du texte libre
        — parse_structured_file_output lève ValueError dans ce cas plutôt
        que de supposer un JSON valide.
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
                response = await client.chat(
                    model=model, messages=messages, format=response_format, stream=False,
                )
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
