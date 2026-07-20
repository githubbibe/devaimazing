"""
Tests de studio.tools.pyenv — vérification syntaxe + import réel des
fichiers Python produits par un agent avant commit.
"""

import sys
from pathlib import Path

import pytest

from studio.tools import pyenv


def test_module_name_simple_file():
    assert pyenv._module_name("backend/schemas.py") == "backend.schemas"


def test_module_name_init_file():
    assert pyenv._module_name("backend/__init__.py") == "backend"


def test_module_name_non_python_file():
    assert pyenv._module_name("backend/requirements.txt") is None


def test_module_name_invalid_identifier():
    assert pyenv._module_name("backend/my-file.py") is None


def test_check_syntax_valid_file():
    files = {"backend/schemas.py": "class TaskCreate:\n    pass\n"}
    assert pyenv.check_syntax(files) is None


def test_check_syntax_invalid_file_reports_line_and_message():
    files = {"backend/main.py": "CORS_ORIGINS = [\n"}
    error = pyenv.check_syntax(files)
    assert error is not None
    assert "backend/main.py" in error
    assert "ligne" in error


def test_check_syntax_ignores_non_python_files():
    files = {"backend/requirements.txt": "this is not ( valid python"}
    assert pyenv.check_syntax(files) is None


async def test_ensure_venv_creates_and_reuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pyenv, "VENV_ROOT", tmp_path / "venvs")

    python_path = await pyenv.ensure_venv("demo-project", requirements_path=None)
    assert python_path.is_file()

    # Deuxième appel : le venv existe déjà, pas de recréation ni d'erreur.
    python_path_again = await pyenv.ensure_venv("demo-project", requirements_path=None)
    assert python_path_again == python_path


async def test_ensure_venv_skips_pip_install_if_requirements_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pyenv, "VENV_ROOT", tmp_path / "venvs")

    called = {"pip": False}

    async def fake_run(*args, **kwargs):
        if "pip" in args:
            called["pip"] = True
        return await real_run(*args, **kwargs)

    real_run = pyenv._run
    monkeypatch.setattr(pyenv, "_run", fake_run)

    await pyenv.ensure_venv("demo-project", requirements_path=tmp_path / "absent-requirements.txt")
    assert called["pip"] is False


async def test_ensure_venv_installs_requirements_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pyenv, "VENV_ROOT", tmp_path / "venvs")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("", encoding="utf-8")  # vide : pip install réussit sans réseau

    pip_calls = []

    async def fake_run(*args, **kwargs):
        if len(args) >= 2 and args[1] == "-m" and "pip" in args:
            pip_calls.append(args)
            return 0, "", ""
        return await real_run(*args, **kwargs)

    real_run = pyenv._run
    monkeypatch.setattr(pyenv, "_run", fake_run)

    await pyenv.ensure_venv("demo-project", requirements_path=requirements)
    assert len(pip_calls) == 1
    assert str(requirements) in pip_calls[0]


async def test_ensure_venv_raises_on_creation_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pyenv, "VENV_ROOT", tmp_path / "venvs")

    async def fake_run(*args, **kwargs):
        return 1, "", "boom"

    monkeypatch.setattr(pyenv, "_run", fake_run)

    with pytest.raises(RuntimeError, match="boom"):
        await pyenv.ensure_venv("demo-project", requirements_path=None)


async def test_check_imports_success(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "ok.py").write_text("import os\nVALUE = 1\n", encoding="utf-8")

    error = await pyenv.check_imports(
        repo_path=tmp_path,
        python_path=Path(sys.executable),
        files={"pkg/ok.py": ""},
    )
    assert error is None


async def test_check_imports_name_error(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "broken.py").write_text(
        "from sqlalchemy import Column\nCOL = Column(Boolean)\n", encoding="utf-8"
    )

    error = await pyenv.check_imports(
        repo_path=tmp_path,
        python_path=Path(sys.executable),
        files={"pkg/broken.py": ""},
    )
    assert error is not None
    assert "pkg.broken" in error


async def test_check_imports_cross_file_import_error(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "schemas.py").write_text("class TaskCreate:\n    pass\n", encoding="utf-8")
    (tmp_path / "pkg" / "crud.py").write_text(
        "from pkg.schemas import TaskCreate, TaskResponse\n", encoding="utf-8"
    )

    error = await pyenv.check_imports(
        repo_path=tmp_path,
        python_path=Path(sys.executable),
        files={"pkg/schemas.py": "", "pkg/crud.py": ""},
    )
    assert error is not None
    assert "pkg.crud" in error


async def test_check_imports_timeout(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "slow.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    error = await pyenv.check_imports(
        repo_path=tmp_path,
        python_path=Path(sys.executable),
        files={"pkg/slow.py": ""},
        timeout_seconds=1,
    )
    assert error is not None
    assert "Timeout" in error


async def test_check_imports_ignores_non_python_files(tmp_path: Path):
    error = await pyenv.check_imports(
        repo_path=tmp_path,
        python_path=Path(sys.executable),
        files={"backend/requirements.txt": ""},
    )
    assert error is None


async def test_verify_python_files_syntax_error_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    called = {"ensure_venv": False}

    async def fake_ensure_venv(*args, **kwargs):
        called["ensure_venv"] = True
        return Path(sys.executable)

    monkeypatch.setattr(pyenv, "ensure_venv", fake_ensure_venv)

    files = {"backend/main.py": "CORS_ORIGINS = [\n"}
    error = await pyenv.verify_python_files(
        repo_path=tmp_path, project_name="demo", files=files
    )
    assert error is not None
    assert called["ensure_venv"] is False


async def test_verify_python_files_no_py_files_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    called = {"ensure_venv": False}

    async def fake_ensure_venv(*args, **kwargs):
        called["ensure_venv"] = True
        return Path(sys.executable)

    monkeypatch.setattr(pyenv, "ensure_venv", fake_ensure_venv)

    files = {"backend/requirements.txt": "fastapi\n"}
    error = await pyenv.verify_python_files(
        repo_path=tmp_path, project_name="demo", files=files
    )
    assert error is None
    assert called["ensure_venv"] is False


async def test_verify_python_files_success_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    async def fake_ensure_venv(*args, **kwargs):
        return Path(sys.executable)

    monkeypatch.setattr(pyenv, "ensure_venv", fake_ensure_venv)

    files = {"pkg/__init__.py": ""}
    error = await pyenv.verify_python_files(
        repo_path=tmp_path, project_name="demo", files=files
    )
    assert error is None
