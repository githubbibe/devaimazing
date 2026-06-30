# Skill - Gestion des erreurs

## Principe

Toute erreur doit être catchée au bon niveau, loggée avec le contexte suffisant
pour le debugging, et propagée ou transformée en réponse utilisateur appropriée.
Jamais de stack trace exposée à l'extérieur du système.

## Hiérarchie des exceptions

Chaque projet définit sa hiérarchie dans `backend/exceptions.py` :

```python
class AimazingError(Exception):
    """Exception de base du projet."""
    def __init__(self, message: str, code: str, details: dict = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)

class ValidationError(AimazingError):
    """Input invalide."""

class NotFoundError(AimazingError):
    """Ressource introuvable."""

class ExternalServiceError(AimazingError):
    """Erreur d'un service externe (LLM, API tierce)."""
```

## Pattern de base

```python
import logging
logger = logging.getLogger(__name__)

def ma_fonction(param: str) -> str:
    try:
        resultat = operation_risquee(param)
        return resultat
    except ExternalServiceError as e:
        logger.error(
            "Échec service externe",
            extra={"code": e.code, "param": param, "details": e.details}
        )
        raise  # Propager si le caller doit gérer
    except Exception as e:
        logger.exception("Erreur inattendue dans ma_fonction", extra={"param": param})
        raise AimazingError(
            message="Erreur interne",
            code="INTERNAL_ERROR"
        ) from e
```

## Règles impératives

1. **Jamais de `except Exception` silencieux** (`except Exception: pass` est interdit).
2. **Toujours loguer avant de lever** ou re-lever une exception.
3. **Jamais de stack trace dans les réponses API**. Transforme en message générique.
4. **Le code d'erreur est une constante string**, pas un entier. Ex : `"USER_NOT_FOUND"`.
5. **Les erreurs de validation** retournent le champ en erreur, pas juste "invalid input".
