"""
Wrapper pour le serveur whisper.cpp local (transcription vocale, ADR 0014).

Whisper est un prétraitement ASR pur, en amont de l'agent Devaimazing — il ne
comprend rien, ne décide de rien, se contente de produire du texte à partir
d'un fichier audio. Le texte résultant est ensuite traité par
devaimazing.agent exactement comme un message tapé (voir ADR 0014,
« Devaimazing ne voit aucune différence... »).

Ollama ne supporte pas Whisper (`ollama pull whisper` échoue avec « pull
model manifest: file does not exist », vérifié le 2026-07-29, voir
docs/roadmap.md) : ce module appelle donc le serveur HTTP `whisper-server`
de whisper.cpp (image `ghcr.io/ggerganov/whisper.cpp`, voir
infra/whisper/), pas Ollama — contrairement à tools/ollama.py.
"""

import asyncio
import time
from typing import Optional

import httpx

from studio.tools.ollama import ExternalServiceError
from studio.tools.tracer import AgentTracer

# Mêmes constantes de retry que tools/ollama.py (skills/retry-patterns.md) —
# un serveur whisper.cpp qui vient de démarrer (modèle en cours de
# chargement) peut refuser des connexions quelques secondes.
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 1.0


async def transcribe_voice_message(
    audio_bytes: bytes,
    *,
    filename: str = "voice.ogg",
    base_url: str = "http://localhost:8090",
    language: str = "fr",
    timeout_seconds: int = 60,
    tracer: Optional[AgentTracer] = None,
) -> str:
    """
    Transcrit un message vocal via le serveur whisper.cpp local.

    Args:
        audio_bytes: Contenu brut du fichier audio (OGG/Opus pour un message
            vocal Telegram — converti côté serveur via `--convert`/ffmpeg,
            voir infra/whisper/compose.yml, aucune conversion nécessaire ici).
        filename: Nom de fichier transmis dans la requête multipart (whisper
            server s'en sert pour détecter le format via ffmpeg si besoin).
        base_url: URL du serveur whisper.cpp (voir infra/whisper/, port hôte
            8090 par défaut — distinct du port Ollama 11434).
        language: Code langue ISO forcé (voir ADR 0014 : usage
            mono-utilisateur francophone, "fr" par défaut — pas
            d'auto-détection pour éviter une transcription dans la mauvaise
            langue sur un audio bruité/court).
        timeout_seconds: Timeout HTTP.
        tracer: AgentTracer optionnel (voir tools.tracer) — mêmes événements
            que run_ollama (llm_call_start/end, retry, error), `backend="whisper"`.

    Returns:
        Texte transcrit (espaces de début/fin retirés). Chaîne vide si
        aucune parole détectée (comportement normal de whisper.cpp, pas une
        erreur).

    Raises:
        ExternalServiceError: Si le serveur whisper.cpp ne répond pas ou
            retourne une erreur après MAX_ATTEMPTS tentatives.
        TimeoutError: Si la transcription dépasse timeout_seconds.

    Side effects:
        Aucun. N'écrit aucun fichier, n'envoie l'audio qu'au serveur
        whisper.cpp local.
    """
    if tracer is not None:
        tracer.emit(
            "llm_call_start", backend="whisper", model=language,
            prompt_chars=len(audio_bytes),
        )

    last_error: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        started_at = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{base_url}/inference",
                    files={"file": (filename, audio_bytes)},
                    data={"language": language, "response_format": "json"},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            if tracer is not None:
                tracer.emit(
                    "error", backend="whisper", model=language,
                    message=f"timeout après {timeout_seconds}s",
                )
            raise TimeoutError(
                f"whisper.cpp n'a pas répondu dans le délai imparti ({timeout_seconds}s)"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500:
                if tracer is not None:
                    tracer.emit(
                        "error", backend="whisper", model=language,
                        message=f"requête rejetée (code {exc.response.status_code})",
                    )
                raise ExternalServiceError(
                    f"whisper.cpp a rejeté la requête (code {exc.response.status_code}) : "
                    f"{exc.response.text}"
                ) from exc
            last_error = exc
        except httpx.HTTPError as exc:
            last_error = exc
        else:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            text = str(data.get("text", "")).strip()
            if tracer is not None:
                tracer.emit(
                    "llm_call_end", backend="whisper", model=language,
                    tokens_prompt=0, tokens_completion=len(text), duration_ms=duration_ms,
                )
            return text

        if attempt < MAX_ATTEMPTS - 1:
            if tracer is not None:
                tracer.emit(
                    "retry", backend="whisper", model=language,
                    attempt=attempt + 1, max_attempts=MAX_ATTEMPTS, error=str(last_error),
                )
            await asyncio.sleep(BASE_DELAY_SECONDS * (2 ** attempt))

    if tracer is not None:
        tracer.emit(
            "error", backend="whisper", model=language,
            message=f"injoignable après {MAX_ATTEMPTS} tentatives",
        )
    raise ExternalServiceError(
        f"whisper.cpp injoignable après {MAX_ATTEMPTS} tentatives ({base_url})"
    ) from last_error
