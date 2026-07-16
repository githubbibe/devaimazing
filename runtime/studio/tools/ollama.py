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

from studio.tools.tracer import AgentTracer

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
    num_ctx: int = 16384,
    response_format: Optional[dict] = None,
    tracer: Optional[AgentTracer] = None,
) -> dict:
    """
    Appelle le modèle Ollama local.

    Args:
        system_prompt: Prompt système de l'agent (contenu de prompts/<agent>.md).
        user_prompt: Prompt utilisateur (contenu de la fiche + skills injectés).
        model: Identifiant du modèle Ollama (ex: qwen2.5:7b-instruct).
        base_url: URL de l'API Ollama.
        timeout_seconds: Timeout en secondes.
        num_ctx: Taille de la fenêtre de contexte (en tokens) demandée à Ollama
            via `options`. Sans ce paramètre, Ollama retombe sur son défaut de
            2048 tokens et tronque silencieusement le début du prompt (system
            prompt + brief + feedback cumulé) dès qu'il dépasse cette taille —
            bug diagnostiqué le 2026-07-16 sur le run todo-list2 (voir
            docs/roadmap.md) : le feedback grossissait à chaque itération mais
            le modèle ne le voyait jamais en entier.
        response_format: Schéma JSON optionnel (voir FILE_OUTPUT_SCHEMA) pour
            contraindre la sortie par grammaire (grammar-constrained decoding,
            Ollama ≥0.5). Si fourni, `content` dans le retour est du texte
            JSON conforme au schéma, à parser (voir
            tools.filesystem.parse_structured_file_output). Si `None` (défaut),
            sortie texte libre inchangée.
        tracer: AgentTracer optionnel (voir tools.tracer) — émet
            llm_call_start/llm_call_end autour de l'appel, un événement
            retry à chaque tentative infructueuse avant nouvel essai
            (jusqu'ici invisibles, voir docs/roadmap.md), error si toutes
            les tentatives échouent. `None` (défaut) : aucune trace émise.

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

    if tracer is not None:
        tracer.emit(
            "llm_call_start", backend="ollama", model=model,
            prompt_chars=len(system_prompt) + len(user_prompt),
        )

    last_error: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        started_at = time.monotonic()
        try:
            async with AsyncClient(host=base_url, timeout=timeout_seconds) as client:
                response = await client.chat(
                    model=model, messages=messages, format=response_format, stream=False,
                    options={"num_ctx": num_ctx},
                )
        except httpx.TimeoutException as exc:
            if tracer is not None:
                tracer.emit(
                    "error", backend="ollama", model=model,
                    message=f"timeout après {timeout_seconds}s",
                )
            raise TimeoutError(
                f"Ollama n'a pas répondu dans le délai imparti ({timeout_seconds}s) "
                f"pour le modèle {model!r}"
            ) from exc
        except ResponseError as exc:
            if exc.status_code in NON_RETRYABLE_STATUS_CODES:
                if tracer is not None:
                    tracer.emit(
                        "error", backend="ollama", model=model,
                        message=f"requête rejetée (code {exc.status_code}) : {exc.error}",
                    )
                raise ExternalServiceError(
                    f"Ollama a rejeté la requête (code {exc.status_code}) : {exc.error}"
                ) from exc
            last_error = exc
        except (ConnectionError, RequestError) as exc:
            last_error = exc
        else:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            if tracer is not None:
                tracer.emit(
                    "llm_call_end", backend="ollama", model=model,
                    tokens_prompt=response.prompt_eval_count or 0,
                    tokens_completion=response.eval_count or 0,
                    duration_ms=duration_ms,
                )
            return {
                "content": response.message.content or "",
                "tokens_prompt": response.prompt_eval_count or 0,
                "tokens_completion": response.eval_count or 0,
                "duration_ms": duration_ms,
            }

        if attempt < MAX_ATTEMPTS - 1:
            if tracer is not None:
                tracer.emit(
                    "retry", backend="ollama", model=model,
                    attempt=attempt + 1, max_attempts=MAX_ATTEMPTS, error=str(last_error),
                )
            await asyncio.sleep(BASE_DELAY_SECONDS * (2 ** attempt))

    if tracer is not None:
        tracer.emit(
            "error", backend="ollama", model=model,
            message=f"injoignable après {MAX_ATTEMPTS} tentatives",
        )
    raise ExternalServiceError(
        f"Ollama injoignable après {MAX_ATTEMPTS} tentatives (modèle {model!r}, {base_url})"
    ) from last_error
