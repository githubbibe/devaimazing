"""
Tests des opérations Git devaimazing.

Utilise de vrais dépôts Git dans des répertoires temporaires (via tmp_path) :
pas de mock, pour vérifier le comportement réel des commandes git invoquées
en sous-process.
"""

import re
import subprocess
from pathlib import Path

import pytest

from studio.tools.git import (
    AGENT_GIT_IDENTITIES,
    commit_as_agent,
    create_run_branch,
    generate_branch_name,
    merge_run_branch,
    slugify_feature_name,
)


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Dépôt git réel, branche develop avec un premier commit."""
    repo_path = tmp_path / "project"
    repo_path.mkdir()
    _git(repo_path, "init", "-q", "-b", "develop")
    _git(repo_path, "config", "user.email", "seed@test.local")
    _git(repo_path, "config", "user.name", "Seed")
    (repo_path / "README.md").write_text("# Projet de test\n", encoding="utf-8")
    _git(repo_path, "add", "README.md")
    _git(repo_path, "commit", "-q", "-m", "chore: initial commit")
    return repo_path


def test_slugify_feature_name():
    assert slugify_feature_name("Features Qui Fait Tout") == "features-qui-fait-tout"
    assert slugify_feature_name("  Ajout Panier & Paiement !  ") == "ajout-panier-paiement"


def test_generate_branch_name_format():
    branch_name = generate_branch_name("features qui fait tout")

    assert re.fullmatch(r"studio/features-qui-fait-tout-[0-9a-f]{5}", branch_name)


def test_generate_branch_name_same_feature_produces_valid_format_each_time():
    first = generate_branch_name("meme nom")
    second = generate_branch_name("meme nom")

    assert first.startswith("studio/meme-nom-")
    assert second.startswith("studio/meme-nom-")


async def test_create_run_branch(repo: Path):
    branch_name = await create_run_branch(repo, "ajout panier", base_branch="develop")

    current_branch = _git(repo, "branch", "--show-current")
    assert current_branch == branch_name
    assert branch_name.startswith("studio/ajout-panier-")


async def test_create_run_branch_unknown_base_raises(repo: Path):
    with pytest.raises(RuntimeError):
        await create_run_branch(repo, "ajout panier", base_branch="branche-inconnue")


async def test_commit_as_agent_creates_commit_with_identity(repo: Path):
    (repo / "backend").mkdir()
    (repo / "backend" / "endpoint.py").write_text("# stub\n", encoding="utf-8")

    commit_hash = await commit_as_agent(
        repo_path=repo,
        agent="back",
        message="feat: add login endpoint stub",
        files=["backend/endpoint.py"],
    )

    assert re.fullmatch(r"[0-9a-f]{40}", commit_hash)
    author = _git(repo, "log", "-1", "--format=%an <%ae>")
    name, email = AGENT_GIT_IDENTITIES["back"]
    assert author == f"{name} <{email}>"
    assert _git(repo, "log", "-1", "--format=%s") == "feat: add login endpoint stub"


async def test_commit_as_agent_unknown_agent_raises(repo: Path):
    with pytest.raises(ValueError):
        await commit_as_agent(repo, agent="inconnu", message="x", files=["README.md"])


async def test_commit_as_agent_empty_files_raises(repo: Path):
    with pytest.raises(ValueError):
        await commit_as_agent(repo, agent="back", message="x", files=[])


async def test_merge_run_branch(repo: Path):
    branch_name = await create_run_branch(repo, "ajout panier", base_branch="develop")
    (repo / "feature.txt").write_text("contenu de la feature\n", encoding="utf-8")
    await commit_as_agent(repo, agent="back", message="feat: add feature", files=["feature.txt"])

    merge_hash = await merge_run_branch(repo, branch_name, target_branch="develop")

    assert re.fullmatch(r"[0-9a-f]{40}", merge_hash)
    assert _git(repo, "branch", "--show-current") == "develop"
    assert (repo / "feature.txt").is_file()
    # La branche du run n'est pas supprimée (traçabilité et audit)
    branches = _git(repo, "branch", "--list", branch_name)
    assert branch_name in branches


async def test_merge_run_branch_conflict_raises(repo: Path):
    branch_name = await create_run_branch(repo, "conflit", base_branch="develop")
    (repo / "README.md").write_text("version branche run\n", encoding="utf-8")
    await commit_as_agent(repo, agent="back", message="feat: edit readme on run branch", files=["README.md"])

    _git(repo, "checkout", "develop")
    (repo / "README.md").write_text("version develop concurrente\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "chore: edit readme on develop")

    with pytest.raises(RuntimeError):
        await merge_run_branch(repo, branch_name, target_branch="develop")
