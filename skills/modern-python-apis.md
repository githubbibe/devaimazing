# Skill - APIs Python récentes (pydantic v2, SQLAlchemy 2.0)

## Contexte

Les modèles locaux (Ollama) confondent régulièrement les APIs pydantic v1 et
SQLAlchemy 1.x (leur entraînement) avec les versions actuelles (pydantic v2,
SQLAlchemy 2.0) — ce guide couvre les pièges les plus fréquents.

## pydantic v2 : BaseSettings a changé de package

`BaseSettings` n'est plus dans `pydantic` — il est dans le package séparé
`pydantic-settings`. Importer `from pydantic import BaseSettings` lève
`PydanticImportError` en v2.

```python
# FAUX (pydantic v1, casse en v2)
from pydantic import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str

    class Config:
        env_file = ".env"

# CORRECT (pydantic v2)
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./app.db")

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)
```

`requirements.txt` doit lister `pydantic-settings` explicitement, en plus de
`pydantic` — ce n'est pas une dépendance transitive.

Toute valeur sans défaut (`Field(...)`) requise à l'import du module (ex.
`settings = Settings()` au niveau module) fait échouer l'import si la
variable d'environnement correspondante n'est pas définie et qu'aucun
fichier `.env` réel n'existe (seulement un `.env.example`) — donner une
valeur par défaut sensée plutôt que `Field(...)` pour toute config qui doit
pouvoir s'importer sans environnement préparé au préalable.

## pydantic v2 : `class Config` reste accepté mais `model_config` est la forme actuelle

Pour `BaseModel` (pas `BaseSettings`), l'ancien style `class Config:` interne
fonctionne encore en v2 (deprecated, pas cassé) — mais préférer
`model_config = ConfigDict(...)` (import depuis `pydantic`) dans du code neuf.

## SQLAlchemy 2.0 : sessions asynchrones

`create_async_engine` doit être associé à un `sessionmaker`/`async_sessionmaker`
utilisé comme **factory** (appelé à chaque requête), jamais instancié une
seule fois au niveau module/`__init__` :

```python
# FAUX — une seule session partagée, jamais fermée
def __init__(self):
    self.session = sessionmaker(bind=engine, class_=AsyncSession)()

# CORRECT — factory, une session par requête/contexte
def __init__(self):
    self.session_factory = sessionmaker(bind=engine, class_=AsyncSession)

async def get_session(self):
    async with self.session_factory() as session:
        yield session
```

Aucune opération asynchrone (`async with`, `await`) dans une méthode qui
n'est pas elle-même `async def` — `SyntaxError: 'async with' outside async
function` si le corps de `__init__` (toujours synchrone) contient du code
async. La création des tables (`Base.metadata.create_all`) doit se faire
dans une méthode `async def` dédiée, appelée explicitement au démarrage de
l'application, pas dans `__init__`.

Une URL de connexion `sqlite:///...` est pour le driver **synchrone** ;
`create_async_engine` exige le driver async explicite :
`sqlite+aiosqlite:///...`.

## Dépendances de test

`pytest` (et `pytest-asyncio`, `httpx` pour tester une API FastAPI async)
sont des dépendances de test à part entière — à déclarer dans
`requirements.txt` dès qu'un fichier de test les importe, pas seulement les
dépendances d'exécution de l'application.
