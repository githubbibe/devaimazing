"""
Tests de studio.tools.live_docs — injection de documentation "vérité
terrain" extraite du venv du projet cible (voir docstring du module).
"""

from pathlib import Path

import pytest

from studio.tools import live_docs, pyenv


async def test_extract_live_docs_returns_empty_if_no_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pyenv, "VENV_ROOT", tmp_path / "venvs")
    assert await live_docs.extract_live_docs("no-such-project") == ""


async def test_extract_live_docs_resolves_stdlib_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pyenv, "VENV_ROOT", tmp_path / "venvs")
    await pyenv.ensure_venv("demo-project", requirements_path=None)

    text = await live_docs.extract_live_docs("demo-project", symbols=["json.loads"])

    assert "### json.loads" in text
    assert "Signature :" in text


async def test_extract_live_docs_skips_unresolvable_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pyenv, "VENV_ROOT", tmp_path / "venvs")
    await pyenv.ensure_venv("demo-project", requirements_path=None)

    text = await live_docs.extract_live_docs(
        "demo-project", symbols=["paquet_inexistant.TrucBidule"]
    )

    assert text == ""


async def test_extract_live_docs_mixes_resolved_and_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(pyenv, "VENV_ROOT", tmp_path / "venvs")
    await pyenv.ensure_venv("demo-project", requirements_path=None)

    text = await live_docs.extract_live_docs(
        "demo-project", symbols=["paquet_inexistant.TrucBidule", "os.path.join"]
    )

    assert "### os.path.join" in text
    assert "paquet_inexistant" not in text
