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
from pathlib import Path
from typing import Optional

from studio.tools.tracer import AgentTracer

VENV_ROOT = Path.home() / ".devaimazing" / "venvs"
IMPORT_TIMEOUT_SECONDS = 10


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
        RuntimeError: Si la création du venv ou `pip install` échoue —
            remonté tel quel, pas de dégradation silencieuse (une
            vérification qui échoue à s'exécuter ne doit pas être confondue
            avec un code qui s'importe correctement).
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
            raise RuntimeError(f"Échec pip install ({requirements_path}) : {stderr.strip()}")
        if tracer is not None:
            tracer.emit("venv_dependencies_installed", requirements=str(requirements_path))

    return python_path


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


def check_syntax(files: dict[str, str]) -> Optional[str]:
    """
    Valide la syntaxe (ast.parse, aucune exécution) des fichiers .py de
    `files`. Retourne le message de la première erreur rencontrée (chemin +
    ligne + message natif), None si tout est syntaxiquement valide.
    """
    for relative_path in sorted(files):
        if not relative_path.endswith(".py"):
            continue
        try:
            ast.parse(files[relative_path], filename=relative_path)
        except SyntaxError as exc:
            return f"Erreur de syntaxe dans {relative_path} ligne {exc.lineno} : {exc.msg}"
    return None


async def check_imports(
    repo_path: Path,
    python_path: Path,
    files: dict[str, str],
    timeout_seconds: float = IMPORT_TIMEOUT_SECONDS,
    tracer: Optional[AgentTracer] = None,
) -> Optional[str]:
    """
    Tente d'importer (venv dédié, cwd=repo_path) chaque fichier .py de
    `files` individuellement — un import transitif (ex. `main.py`
    important `routers`, `crud`, `schemas`...) révèle aussi les
    ImportError/NameError situés dans les fichiers dépendants, pas
    seulement dans le fichier importé lui-même.

    Returns:
        Message de la première erreur rencontrée (dernière ligne utile du
        stderr), None si tous les imports réussissent (ou si `files` ne
        contient aucun fichier .py importable).
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
        else:
            stripped = stderr.strip()
            last_line = stripped.splitlines()[-1] if stripped else "erreur inconnue"
            message = f"Échec d'import de {module_name} ({relative_path}) : {last_line}"
        if tracer is not None:
            tracer.emit(
                "import_check_failed",
                module=module_name,
                path=relative_path,
                stderr=stderr[-2000:],
            )
        return message
    return None


async def verify_python_files(
    repo_path: Path,
    project_name: str,
    files: dict[str, str],
    requirements_relative: Optional[str] = None,
    tracer: Optional[AgentTracer] = None,
) -> Optional[str]:
    """
    Point d'entrée : syntaxe puis import réel. Retourne le message de la
    première erreur rencontrée, None si tout est valide (y compris si
    `files` ne contient aucun fichier .py — no-op, ex. sortie de `front`).
    """
    syntax_error = check_syntax(files)
    if syntax_error is not None:
        return syntax_error

    if not any(path.endswith(".py") for path in files):
        return None

    requirements_path = (repo_path / requirements_relative) if requirements_relative else None
    python_path = await ensure_venv(project_name, requirements_path, tracer=tracer)
    return await check_imports(repo_path, python_path, files, tracer=tracer)
