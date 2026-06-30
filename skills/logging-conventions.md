# Skill - Conventions de logging

## Configuration

```python
import logging
import json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }
        if hasattr(record, "__dict__"):
            extra = {k: v for k, v in record.__dict__.items()
                     if k not in logging.LogRecord.__dict__}
            log_data.update(extra)
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, default=str)
```

## Niveaux

| Niveau | Usage |
|---|---|
| `DEBUG` | Détails internes utiles au développement, désactivé en prod |
| `INFO` | Événements métier normaux (requête reçue, traitement terminé) |
| `WARNING` | Situation anormale mais récupérée (retry, fallback déclenché) |
| `ERROR` | Erreur traitée mais qui impacte l'utilisateur |
| `CRITICAL` | Erreur système nécessitant intervention immédiate |

## Champs obligatoires dans `extra`

```python
logger.info(
    "Message descriptif en français ou anglais cohérent avec le projet",
    extra={
        "user_id": user_id,        # si applicable
        "run_id": run_id,          # si dans un run devaimazing
        "duration_ms": duration,   # si mesure de performance
        "agent": "back",           # si agent devaimazing
    }
)
```

## Ce qu'on ne logue JAMAIS

- Mots de passe, tokens, clés API
- Données personnelles complètes (email, nom : utilise un hash ou un ID)
- Stack traces complètes en production (niveau DEBUG uniquement)
