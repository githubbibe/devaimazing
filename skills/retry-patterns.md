# Skill - Patterns de résilience et retry

## Pattern retry avec backoff exponentiel

```python
import asyncio
import logging
from functools import wraps

logger = logging.getLogger(__name__)

def retry(max_attempts: int = 3, base_delay: float = 1.0, exceptions: tuple = (Exception,)):
    """
    Décorateur retry avec backoff exponentiel.
    
    Args:
        max_attempts: Nombre maximum de tentatives (défaut: 3)
        base_delay: Délai initial en secondes, doublé à chaque retry (défaut: 1.0)
        exceptions: Tuple des exceptions qui déclenchent un retry
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"Tentative {attempt + 1}/{max_attempts} échouée, retry dans {delay}s",
                            extra={"function": func.__name__, "error": str(e), "attempt": attempt + 1}
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"Toutes les tentatives épuisées ({max_attempts})",
                            extra={"function": func.__name__, "error": str(e)}
                        )
            raise last_exception
        return wrapper
    return decorator
```

## Usage

```python
from backend.utils.retry import retry
from backend.exceptions import ExternalServiceError

@retry(max_attempts=3, base_delay=1.0, exceptions=(ExternalServiceError,))
async def call_ollama(prompt: str) -> str:
    ...
```

## Timeouts

Toujours définir un timeout sur les appels externes :

```python
import httpx

async with httpx.AsyncClient(timeout=30.0) as client:
    response = await client.post(url, json=payload)
```

## Ce qu'on ne retente PAS

- Erreurs de validation (4xx) : inutile, le résultat sera identique
- Erreurs d'authentification (401, 403) : un retry ne changera rien
- Erreurs de ressource non trouvée (404) : inutile
