"""
Vérification syntaxe + import réel des fichiers Python produits par un agent
(Back, Front, Test), avant commit.

Gap trouvé en run réel le 2026-07-19/20 sur run-20260714-205712 (todo-list,
voir docs/roadmap.md) : back/back-tu régénèrent l'intégralité de leur
périmètre à chaque tour sans aucune vérification avant de committer — les
erreurs de syntaxe (fichier tronqué) et surtout d'import (NameError,
ImportError sur des symboles manquants entre fichiers) n'étaient détectées
que par l'audit Architecte (coûteux, tardif, un tour de boucle complet
perdu). 22 cycles sur 2 modèles (qwen2.5:7b puis 14b) sans converger, avec
les mêmes régressions répétées.

Un venv dédié par projet (~/.devaimazing/venvs/<project_name>/) est créé au
premier besoin et réutilisé ; les dépendances du repo cible sont installées
depuis son requirements.txt avant chaque vérification (no-op si absent).
"""

import ast
import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from studio.tools.tracer import AgentTracer

VENV_ROOT = Path.home() / ".devaimazing" / "venvs"
IMPORT_TIMEOUT_SECONDS = 10
_MAX_ERROR_CHARS = 300


@dataclass
class VerifyFailure:
    """
    Échec de vérification identifiant le fichier fautif avec certitude —
    distingué du blocage `blocked_reason` de l'agent lui-même (texte libre,
    fichier non identifiable) pour permettre une réécriture ciblée au tour
    suivant (voir studio.state.StudioState.retry_scope, gap trouvé en run
    réel le 2026-07-20 sur run-20260714-205712 : un fix manuel sur un seul
    fichier était écrasé par la régénération complète du tour suivant).

    `related_files` : autres fichiers du repo cible mentionnés dans la
    traceback (import transitif — ex. `crud.py` importe `models.py` qui
    échoue réellement à cause d'un import circulaire avec `database.py`) —
    sans eux, le mode ciblé montrerait au modèle un fichier qui n'est pas
    en cause et qui ne peut donc jamais corriger le vrai bug. Gap trouvé en
    run réel le 2026-07-20 (même run) : 6 tours sur 8 ont échoué à
    l'identique sur `backend/crud.py` alors que le bug était dans
    `models.py`/`database.py`, jamais montrés au modèle.
    """
    file: str
    message: str
    related_files: list[str] = field(default_factory=list)


class DependencyInstallError(Exception):
    """
    `pip install` a échoué à cause du contenu de requirements.txt (ex.
    version pinnée inexistante sur PyPI, hallucinée par l'agent) — un bug
    du code produit, pas un problème d'environnement. Distingué du
    RuntimeError générique (création du venv : disque, permissions) pour
    que verify_python_files le route vers feedback_sent au lieu de faire
    interrompre le run — gap trouvé en run réel le 2026-07-20 sur
    run-20260714-205712 (todo-list) : `fastapi==0.95.3` n'existe pas,
    `pip install` faisait planter le run en RuntimeError générique, un
    `retry` manuel était nécessaire alors que l'agent aurait pu corriger
    lui-même sa version au tour suivant.
    """


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / "bin" / "python"


async def _run(
    *args: str, cwd: Optional[Path] = None, timeout: Optional[float] = None
) -> tuple[int, str, str]:
    """
    Exécute une commande, retourne (returncode, stdout, stderr). En cas de
    timeout : returncode=-1, stderr="timeout" (sentinelle distinguée d'une
    vraie erreur d'exécution par les appelants).
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return -1, "", "timeout"
    return (
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def ensure_venv(
    project_name: str,
    requirements_path: Optional[Path],
    tracer: Optional[AgentTracer] = None,
) -> Path:
    """
    Crée (si absent) le venv dédié à `project_name` et y installe les
    dépendances de `requirements_path` (no-op si le fichier n'existe pas
    encore — cas du tout premier stub, avant que back n'ait créé son propre
    requirements.txt).

    Returns:
        Chemin de l'exécutable python du venv.

    Raises:
        RuntimeError: Si la création du venv échoue (disque, permissions) —
            problème d'environnement, remonté tel quel, pas de dégradation
            silencieuse.
        DependencyInstallError: Si `pip install` échoue — problème de
            contenu (requirements.txt produit par l'agent), à distinguer
            d'une erreur d'environnement par l'appelant (voir
            verify_python_files).
    """
    venv_dir = VENV_ROOT / project_name
    python_path = _venv_python(venv_dir)
    if not python_path.is_file():
        VENV_ROOT.mkdir(parents=True, exist_ok=True)
        returncode, _, stderr = await _run("python3", "-m", "venv", str(venv_dir))
        if returncode != 0:
            raise RuntimeError(f"Échec de création du venv {venv_dir} : {stderr.strip()}")
        if tracer is not None:
            tracer.emit("venv_created", path=str(venv_dir))

    if requirements_path is not None and requirements_path.is_file():
        returncode, _, stderr = await _run(
            str(python_path), "-m", "pip", "install", "-q", "-r", str(requirements_path)
        )
        if returncode != 0:
            stripped = stderr.strip()
            # Une seule ligne utile suffit en général (ex. "ERROR: No matching
            # distribution found for X") — au-delà de _MAX_ERROR_CHARS (pip
            # concatène parfois la liste complète des versions disponibles
            # sur une seule ligne, potentiellement des Ko), tronquer plutôt
            # que de bloater davantage la fiche (voir docs/roadmap.md,
            # feedback déjà cité comme cause de non-convergence).
            last_line = stripped.splitlines()[-1] if stripped else "erreur inconnue"
            if len(last_line) > _MAX_ERROR_CHARS:
                last_line = last_line[:_MAX_ERROR_CHARS] + "… (tronqué)"
            if tracer is not None:
                tracer.emit(
                    "dependency_install_failed",
                    requirements=str(requirements_path),
                    stderr=stderr[-2000:],
                )
            raise DependencyInstallError(
                f"Échec pip install ({requirements_path}) : {last_line}"
            )
        if tracer is not None:
            tracer.emit("venv_dependencies_installed", requirements=str(requirements_path))

    return python_path


def extract_traceback_files(stderr: str, repo_path: Path) -> list[str]:
    """
    Extrait les fichiers du repo cible mentionnés dans une traceback Python
    (lignes `File "..."`) — hors pseudo-fichiers (`<string>`, `<frozen
    importlib._bootstrap>`) et hors fichiers extérieurs au repo (stdlib,
    site-packages), que l'agent ne peut de toute façon pas corriger.

    Returns:
        Chemins relatifs à repo_path, dans l'ordre d'apparition dans la
        traceback, dédupliqués.

    Example:
        >>> extract_traceback_files(
        ...     'File "<string>", line 1, in <module>\\n'
        ...     'File "/repo/backend/crud.py", line 4, in <module>\\n'
        ...     'File "/repo/backend/models.py", line 2, in <module>',
        ...     Path("/repo"),
        ... )
        ['backend/crud.py', 'backend/models.py']
    """
    seen: list[str] = []
    for match in re.findall(r'File "([^"]+)"', stderr):
        if match.startswith("<"):
            continue
        try:
            relative = Path(match).relative_to(repo_path)
        except ValueError:
            continue
        relative_str = relative.as_posix()
        if relative_str not in seen:
            seen.append(relative_str)
    return seen


def _module_name(relative_path: str) -> Optional[str]:
    """
    Chemin relatif -> nom de module importable.

    Example:
        >>> _module_name("backend/schemas.py")
        'backend.schemas'
        >>> _module_name("backend/__init__.py")
        'backend'
        >>> _module_name("backend/requirements.txt")
    """
    if not relative_path.endswith(".py"):
        return None
    parts = relative_path[: -len(".py")].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def check_syntax(files: dict[str, str]) -> Optional[VerifyFailure]:
    """
    Valide la syntaxe (ast.parse, aucune exécution) des fichiers .py de
    `files`. Retourne le VerifyFailure de la première erreur rencontrée
    (chemin + ligne + message natif), None si tout est syntaxiquement valide.
    """
    for relative_path in sorted(files):
        if not relative_path.endswith(".py"):
            continue
        try:
            ast.parse(files[relative_path], filename=relative_path)
        except SyntaxError as exc:
            return VerifyFailure(
                file=relative_path,
                message=f"Erreur de syntaxe dans {relative_path} ligne {exc.lineno} : {exc.msg}",
            )
    return None


async def check_imports(
    repo_path: Path,
    python_path: Path,
    files: dict[str, str],
    timeout_seconds: float = IMPORT_TIMEOUT_SECONDS,
    tracer: Optional[AgentTracer] = None,
) -> Optional[VerifyFailure]:
    """
    Tente d'importer (venv dédié, cwd=repo_path) chaque fichier .py de
    `files` individuellement — un import transitif (ex. `main.py`
    important `routers`, `crud`, `schemas`...) révèle aussi les
    ImportError/NameError situés dans les fichiers dépendants, pas
    seulement dans le fichier importé lui-même. `VerifyFailure.file` reste
    le fichier IMPORTÉ (ex. `backend/main.py`) ; les autres fichiers du
    repo présents dans la traceback (le(s) fichier(s) réellement en cause)
    sont dans `VerifyFailure.related_files` — voir `extract_traceback_files`.

    Returns:
        VerifyFailure de la première erreur rencontrée (dernière ligne
        utile du stderr comme message, tous les fichiers repo de la
        traceback comme related_files), None si tous les imports
        réussissent (ou si `files` ne contient aucun fichier .py
        importable).
    """
    for relative_path in sorted(files):
        module_name = _module_name(relative_path)
        if module_name is None:
            continue
        returncode, _, stderr = await _run(
            str(python_path),
            "-c",
            f"import {module_name}",
            cwd=repo_path,
            timeout=timeout_seconds,
        )
        if returncode == 0:
            continue
        if stderr == "timeout":
            message = (
                f"Timeout ({timeout_seconds}s) à l'import de {module_name} "
                f"({relative_path}) — effet de bord probable au niveau module."
            )
            related_files: list[str] = []
        else:
            stripped = stderr.strip()
            last_line = stripped.splitlines()[-1] if stripped else "erreur inconnue"
            if len(last_line) > _MAX_ERROR_CHARS:
                last_line = last_line[:_MAX_ERROR_CHARS] + "… (tronqué)"
            message = f"Échec d'import de {module_name} ({relative_path}) : {last_line}"
            related_files = [
                f for f in extract_traceback_files(stderr, repo_path) if f != relative_path
            ]
        if tracer is not None:
            tracer.emit(
                "import_check_failed",
                module=module_name,
                path=relative_path,
                related_files=related_files,
                stderr=stderr[-2000:],
            )
        return VerifyFailure(file=relative_path, message=message, related_files=related_files)
    return None


async def verify_python_files(
    repo_path: Path,
    project_name: str,
    files: dict[str, str],
    requirements_relative: Optional[str] = None,
    tracer: Optional[AgentTracer] = None,
) -> Optional[VerifyFailure]:
    """
    Point d'entrée : syntaxe puis import réel. Retourne le VerifyFailure de
    la première erreur rencontrée, None si tout est valide (y compris si
    `files` ne contient aucun fichier .py — no-op, ex. sortie de `front`).
    """
    syntax_error = check_syntax(files)
    if syntax_error is not None:
        return syntax_error

    if not any(path.endswith(".py") for path in files):
        return None

    requirements_path = (repo_path / requirements_relative) if requirements_relative else None
    try:
        python_path = await ensure_venv(project_name, requirements_path, tracer=tracer)
    except DependencyInstallError as exc:
        # Bug de contenu (requirements.txt produit par l'agent) : traité
        # comme les autres erreurs de vérification (feedback_sent), pas
        # comme une erreur d'environnement — voir DependencyInstallError.
        # RuntimeError (échec de création du venv) n'est PAS capturé ici :
        # ça reste une vraie erreur d'infra, remontée telle quelle.
        return VerifyFailure(file=requirements_relative or "requirements.txt", message=str(exc))
    return await check_imports(repo_path, python_path, files, tracer=tracer)
