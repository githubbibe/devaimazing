"""
Injection de documentation "vérité terrain" extraite à l'exécution du venv
du projet cible, pour les symboles Python qui reviennent le plus souvent en
cause dans les erreurs de génération (confusion pydantic v1/v2, SQLAlchemy
1.x/2.x — voir skills/modern-python-apis.md). Ce skill est un texte écrit à
la main : correct au moment où il a été écrit, mais rien ne garantit qu'il
reste vrai si les versions épinglées du projet cible changent. Ici, on
interroge directement le paquet réellement installé (inspect.signature +
docstring), donc toujours aligné sur ce qui tourne vraiment.

Mesure provisoire (2026-08-06, voir docs/roadmap.md) : la réponse long
terme à ce problème plus général — donner à un modèle la connaissance de
librairies postérieures à son entraînement — mériterait un service dédié,
hors périmètre de ce runtime pour l'instant (voir ADR 0006).

Lecture seule : n'installe et ne crée jamais de venv (voir
tools.pyenv.existing_venv_python) — si le venv du projet n'existe pas
encore (toute première activation, avant que Back ait produit son
requirements.txt), extract_live_docs retourne une chaîne vide plutôt que
d'échouer ou de forcer une installation prématurée. Best-effort à tous les
étages : un symbole absent, un import qui échoue, un venv corrompu ne
lèvent jamais — ce contexte enrichit le prompt, il ne conditionne aucune
logique de routing.
"""

from typing import Optional

from studio.tools.pyenv import _run, existing_venv_python

# Symboles connus pour déclencher des confusions de version récurrentes
# chez les modèles locaux (voir docs/roadmap.md, 2026-08-06 — cascade de 4
# modèles, tous ont produit `from pydantic import BaseSettings` ou un
# équivalent SQLAlchemy sync/async incohérent à un moment ou un autre).
DEFAULT_SYMBOLS = [
    "pydantic_settings.BaseSettings",
    "pydantic.field_validator",
    "pydantic.ConfigDict",
    "sqlalchemy.ext.asyncio.create_async_engine",
    "sqlalchemy.ext.asyncio.async_sessionmaker",
]

_EXTRACT_TEMPLATE = '''
import importlib, inspect
dotted = "{symbol}"
module_path, _, name = dotted.rpartition(".")
try:
    module = importlib.import_module(module_path)
    obj = getattr(module, name)
except Exception:
    pass
else:
    print("###", dotted)
    try:
        sig = str(inspect.signature(obj))
    except (TypeError, ValueError):
        sig = ""
    if sig:
        print("Signature :", sig[:300] + ("..." if len(sig) > 300 else ""))
    doc = inspect.getdoc(obj)
    if doc:
        print(doc.strip().split(chr(10) + chr(10))[0])
'''


async def extract_live_docs(
    project_name: str,
    symbols: Optional[list[str]] = None,
    timeout_seconds: float = 10.0,
) -> str:
    """
    Extrait signature + premier paragraphe de docstring pour chaque symbole
    de `symbols` (DEFAULT_SYMBOLS si omis), depuis le venv déjà créé pour
    `project_name`.

    Args:
        project_name: Nom du projet cible (StudioConfig.project_name),
            utilisé pour retrouver ~/.devaimazing/venvs/<project_name>/.
        symbols: Chemins pointés complets (ex. "pydantic.field_validator").
        timeout_seconds: Timeout par symbole (pas global) — un symbole lent
            à importer ne doit pas priver les autres de leur chance.

    Returns:
        Bloc de texte (un paragraphe par symbole résolu, séparés par une
        ligne vide), ou chaîne vide si le venv n'existe pas encore ou si
        aucun symbole n'a pu être résolu.
    """
    python_path = existing_venv_python(project_name)
    if python_path is None:
        return ""

    blocks = []
    for symbol in symbols or DEFAULT_SYMBOLS:
        code = _EXTRACT_TEMPLATE.format(symbol=symbol)
        returncode, stdout, _ = await _run(str(python_path), "-c", code, timeout=timeout_seconds)
        text = stdout.strip()
        if returncode == 0 and text:
            blocks.append(text)
    return "\n\n".join(blocks)
