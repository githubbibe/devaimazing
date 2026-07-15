"""
Tests des opérations Git devaimazing.

Utilise de vrais dépôts Git dans des répertoires temporaires (via tmp_path) :
pas de mock, pour vérifier le comportement réel des commandes git invoquées
en sous-process. Exception : create_github_remote, qui appellerait le vrai
binaire `gh` et créerait un repo GitHub réel — sous-process scripté à la
place (même pattern que test_claude_code.py).
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

import studio.tools.git as git_tool
from studio.tools.git import (
    AGENT_GIT_IDENTITIES,
    commit_as_agent,
    create_github_remote,
    create_initial_commit,
    create_run_branch,
    generate_branch_name,
    init_repo,
    merge_run_branch,
    push_branch,
    slugify_feature_name,
)
from studio.tools.tracer import RunTracer


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


async def test_commit_as_agent_emits_commit_event(repo: Path, tmp_path: Path):
    (repo / "backend").mkdir()
    (repo / "backend" / "endpoint.py").write_text("# stub\n", encoding="utf-8")
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("back", "STUBS")

    commit_hash = await commit_as_agent(
        repo_path=repo,
        agent="back",
        message="feat: add login endpoint stub",
        files=["backend/endpoint.py"],
        tracer=tracer,
    )

    events = [json.loads(l) for l in tracer._tracer.trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["event"] == "commit"
    assert events[0]["hash"] == commit_hash
    assert events[0]["git_identity"] == "back"
    assert events[0]["files"] == ["backend/endpoint.py"]


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


async def test_init_repo_creates_develop_branch(tmp_path: Path):
    repo_path = tmp_path / "nouveau-projet"
    repo_path.mkdir()

    await init_repo(repo_path, initial_branch="develop")

    assert (repo_path / ".git").is_dir()
    assert _git(repo_path, "symbolic-ref", "--short", "HEAD") == "develop"


async def test_init_repo_is_idempotent(tmp_path: Path):
    """git init est lui-même idempotent (« Reinitialized existing... ») — pas d'erreur à gérer ici."""
    repo_path = tmp_path / "deja-un-repo"
    repo_path.mkdir()
    await init_repo(repo_path, initial_branch="develop")

    await init_repo(repo_path, initial_branch="develop")

    assert (repo_path / ".git").is_dir()


async def test_create_initial_commit_writes_readme_and_commits(tmp_path: Path):
    repo_path = tmp_path / "nouveau-projet"
    repo_path.mkdir()
    await init_repo(repo_path, initial_branch="develop")

    commit_hash = await create_initial_commit(repo_path, "mon-projet")

    assert re.fullmatch(r"[0-9a-f]{40}", commit_hash)
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "# mon-projet\n"
    assert _git(repo_path, "log", "-1", "--format=%s") == "chore: initialise mon-projet"
    author = _git(repo_path, "log", "-1", "--format=%an <%ae>")
    assert author == "devaimazing-bootstrap <bootstrap@aimazing.fr>"


async def test_push_branch_no_remote_raises(tmp_path: Path):
    repo_path = tmp_path / "nouveau-projet"
    repo_path.mkdir()
    await init_repo(repo_path, initial_branch="develop")
    await create_initial_commit(repo_path, "mon-projet")

    with pytest.raises(RuntimeError):
        await push_branch(repo_path, "develop")


class _FakeGhProcess:
    def __init__(self, returncode: int, stderr: bytes = b""):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return b"", self._stderr


async def test_create_github_remote_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo_path = tmp_path / "nouveau-projet"
    repo_path.mkdir()
    captured_args: list = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        captured_args.extend(args)
        return _FakeGhProcess(returncode=0)

    monkeypatch.setattr(git_tool.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    await create_github_remote(repo_path, "mon-projet", private=True)

    assert captured_args[:3] == ["gh", "repo", "create"]
    assert "mon-projet" in captured_args
    assert "--private" in captured_args


async def test_create_github_remote_failure_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo_path = tmp_path / "nouveau-projet"
    repo_path.mkdir()

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeGhProcess(returncode=1, stderr=b"name already taken")

    monkeypatch.setattr(git_tool.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match="name already taken"):
        await create_github_remote(repo_path, "mon-projet")
