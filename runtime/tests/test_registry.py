"""
Tests de studio.tools.registry (ADR 0013, Décision 4).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import studio.tools.queries as queries_module
import studio.tools.registry as registry_module
import yaml
from studio.tools.registry import (
    TOOL_REGISTRY,
    ToolSpec,
    execute_tool,
    parse_slash_command,
    to_ollama_tool,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


# Table de l'ADR 0013, Décision 4, complétée par les outils ajoutés pour
# l'ADR 0015 (new_feature/valider_fiche_feature phase 1,
# new_project/valider_fiche_projet phase 2) — un des points les plus
# structurants des deux ADR, vérifié explicitement outil par outil pour ne
# pas dériver en silence d'une future édition de registry.py.
_ADR_TABLE = {
    "lire_statut": (False, False, False),
    "lire_progression": (False, False, False),
    "lister_projets": (False, False, False),
    "creer_projet": (False, False, False),
    "archive_projet": (True, True, True),
    "new_feature": (False, False, False),
    "valider_fiche_feature": (False, True, False),
    "new_project": (False, False, False),
    "valider_fiche_projet": (False, True, False),
    "reject_checkpoint": (True, True, True),
    "stop_run": (True, True, True),
}


def test_tool_registry_matches_adr_table():
    assert set(TOOL_REGISTRY) == set(_ADR_TABLE)
    for name, (destructif, requiert_confirmation, sauvegarde_avant) in _ADR_TABLE.items():
        spec = TOOL_REGISTRY[name]
        assert spec.destructif is destructif, name
        assert spec.requiert_confirmation is requiert_confirmation, name
        assert spec.sauvegarde_avant is sauvegarde_avant, name


async def test_execute_tool_needs_confirmation_does_not_call_handler(
    monkeypatch: pytest.MonkeyPatch,
):
    called = []

    async def handler(config, **kwargs):
        called.append(kwargs)
        return {}

    spec = ToolSpec(
        name="test_tool", description="x", parameters={"type": "object", "properties": {}},
        destructif=True, requiert_confirmation=True, sauvegarde_avant=True, handler=handler,
    )
    monkeypatch.setitem(registry_module.TOOL_REGISTRY, "test_tool", spec)

    result = await execute_tool("test_tool", {}, config=SimpleNamespace(), confirmed=False)

    assert result.status == "needs_confirmation"
    assert called == []


async def test_execute_tool_confirmed_calls_handler(monkeypatch: pytest.MonkeyPatch):
    async def handler(config, **kwargs):
        return {"echo": kwargs}

    spec = ToolSpec(
        name="test_tool", description="x", parameters={"type": "object", "properties": {}},
        destructif=True, requiert_confirmation=True, sauvegarde_avant=True, handler=handler,
    )
    monkeypatch.setitem(registry_module.TOOL_REGISTRY, "test_tool", spec)

    result = await execute_tool(
        "test_tool", {"run_id": "run-1"}, config=SimpleNamespace(), confirmed=True
    )

    assert result.status == "ok"
    assert result.data == {
        "echo": {"run_id": "run-1", "bot": None, "chat_id": None, "message_thread_id": None}
    }


async def test_execute_tool_forwards_bot_and_chat_id_to_handler(monkeypatch: pytest.MonkeyPatch):
    async def handler(config, **kwargs):
        return {"echo": kwargs}

    spec = ToolSpec(
        name="test_tool", description="x", parameters={"type": "object", "properties": {}},
        destructif=False, requiert_confirmation=False, sauvegarde_avant=False, handler=handler,
    )
    monkeypatch.setitem(registry_module.TOOL_REGISTRY, "test_tool", spec)
    fake_bot = object()

    result = await execute_tool(
        "test_tool", {}, config=SimpleNamespace(), bot=fake_bot, chat_id=42
    )

    assert result.data == {
        "echo": {"bot": fake_bot, "chat_id": 42, "message_thread_id": None}
    }


async def test_execute_tool_no_confirmation_required_calls_handler_directly(
    monkeypatch: pytest.MonkeyPatch,
):
    async def handler(config, **kwargs):
        return {"ok": True}

    spec = ToolSpec(
        name="test_tool", description="x", parameters={"type": "object", "properties": {}},
        destructif=False, requiert_confirmation=False, sauvegarde_avant=False, handler=handler,
    )
    monkeypatch.setitem(registry_module.TOOL_REGISTRY, "test_tool", spec)

    result = await execute_tool("test_tool", {}, config=SimpleNamespace(), confirmed=False)

    assert result.status == "ok"


async def test_execute_tool_not_implemented_returns_error():
    result = await execute_tool(
        "stop_run", {"run_id": "run-1"}, config=SimpleNamespace(), confirmed=True
    )

    assert result.status == "error"


async def test_execute_tool_missing_required_arg_returns_error():
    result = await execute_tool("lire_statut", {}, config=SimpleNamespace(), confirmed=False)

    assert result.status == "error"
    assert "run_id" in result.summary


async def test_execute_tool_unknown_tool_returns_error():
    result = await execute_tool("inexistant", {}, config=SimpleNamespace(), confirmed=False)

    assert result.status == "error"


def test_to_ollama_tool_shape():
    spec = TOOL_REGISTRY["lire_statut"]

    tool = to_ollama_tool(spec)

    assert tool == {
        "type": "function",
        "function": {
            "name": "lire_statut",
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def test_parse_slash_command_known():
    assert parse_slash_command("/status run-042") == ("lire_statut", {"run_id": "run-042"})


def test_parse_slash_command_unknown_command():
    assert parse_slash_command("/inconnu") is None


def test_parse_slash_command_not_a_slash():
    assert parse_slash_command("arrête le run") is None


async def test_lire_statut_handler_delegates_to_queries(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_run_snapshot(config, run_id):
        return {"found": True, "run_id": run_id}

    monkeypatch.setattr(queries_module, "get_run_snapshot", fake_get_run_snapshot)

    result = await execute_tool(
        "lire_statut", {"run_id": "run-042"}, config=SimpleNamespace(), confirmed=False
    )

    assert result.status == "ok"
    assert result.data == {"found": True, "run_id": "run-042"}


async def test_lister_projets_handler_delegates_to_queries(monkeypatch: pytest.MonkeyPatch):
    async def fake_list_projects(config_dir):
        return ["demo"]

    monkeypatch.setattr(queries_module, "list_projects", fake_list_projects)

    result = await execute_tool(
        "lister_projets", {}, config=SimpleNamespace(config_dir="/whatever"), confirmed=False
    )

    assert result.status == "ok"
    assert result.data == {"projects": ["demo"]}


# --- creer_projet ---

async def test_creer_projet_creates_topic_and_writes_thread_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "projects" / "demo.yml", {"name": "demo"})

    async def fake_create_forum_topic(chat_id, name):
        assert chat_id == 111
        assert name == "demo"
        return SimpleNamespace(message_thread_id=999)

    fake_bot = SimpleNamespace(create_forum_topic=fake_create_forum_topic)

    captured = {}

    async def fake_set_project_thread_id(path, thread_id):
        captured["path"] = path
        captured["thread_id"] = thread_id

    monkeypatch.setattr(registry_module, "set_project_thread_id", fake_set_project_thread_id)

    result = await execute_tool(
        "creer_projet", {"name": "demo"},
        config=SimpleNamespace(config_dir=config_dir),
        bot=fake_bot, chat_id=111,
    )

    assert result.status == "ok"
    assert result.data == {"project": "demo", "thread_id": 999}
    assert captured["thread_id"] == 999


async def test_creer_projet_unknown_project_returns_error(tmp_path: Path):
    config_dir = tmp_path / "config"
    (config_dir / "projects").mkdir(parents=True)

    result = await execute_tool(
        "creer_projet", {"name": "inconnu"},
        config=SimpleNamespace(config_dir=config_dir),
        bot=SimpleNamespace(), chat_id=111,
    )

    assert result.status == "error"


async def test_creer_projet_without_bot_context_returns_error(tmp_path: Path):
    result = await execute_tool(
        "creer_projet", {"name": "demo"}, config=SimpleNamespace(config_dir=tmp_path),
    )

    assert result.status == "error"


# --- archive_projet ---

@pytest.fixture
def demo_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {"models": {}})
    _write_yaml(config_dir / "projects" / "demo.yml", {
        "repo_path": str(tmp_path / "demo-repo"),
        "telegram": {"thread_id": 777},
    })
    return config_dir


def _patch_archive_git(monkeypatch: pytest.MonkeyPatch, *, commit_hash):
    calls: dict = {}

    async def fake_commit_safety_snapshot(repo_path, message, tracer=None):
        calls["commit_repo_path"] = repo_path
        return commit_hash

    async def fake_current_branch(repo_path):
        return "studio/demo-feature"

    async def fake_push_branch(repo_path, branch, remote="origin"):
        calls["push"] = (repo_path, branch)

    monkeypatch.setattr(registry_module, "commit_safety_snapshot", fake_commit_safety_snapshot)
    monkeypatch.setattr(registry_module, "current_branch", fake_current_branch)
    monkeypatch.setattr(registry_module, "push_branch", fake_push_branch)
    return calls


async def test_archive_projet_requires_confirmation(demo_config_dir: Path):
    result = await execute_tool(
        "archive_projet", {"name": "demo"},
        config=SimpleNamespace(config_dir=demo_config_dir),
        bot=SimpleNamespace(), chat_id=222,
    )

    assert result.status == "needs_confirmation"


async def test_archive_projet_commits_pushes_and_closes_topic(
    monkeypatch: pytest.MonkeyPatch, demo_config_dir: Path
):
    calls = _patch_archive_git(monkeypatch, commit_hash="abc123")

    async def fake_close_forum_topic(chat_id, message_thread_id):
        calls["closed"] = (chat_id, message_thread_id)
        return True

    fake_bot = SimpleNamespace(close_forum_topic=fake_close_forum_topic)

    result = await execute_tool(
        "archive_projet", {"name": "demo"},
        config=SimpleNamespace(config_dir=demo_config_dir),
        bot=fake_bot, chat_id=222, confirmed=True,
    )

    assert result.status == "ok"
    assert result.data == {"project": "demo", "commit": "abc123", "thread_id": 777}
    assert calls["push"] == (calls["commit_repo_path"], "studio/demo-feature")
    assert calls["closed"] == (222, 777)


async def test_archive_projet_no_changes_skips_push(
    monkeypatch: pytest.MonkeyPatch, demo_config_dir: Path
):
    calls = _patch_archive_git(monkeypatch, commit_hash=None)

    async def fake_close_forum_topic(chat_id, message_thread_id):
        calls["closed"] = (chat_id, message_thread_id)
        return True

    fake_bot = SimpleNamespace(close_forum_topic=fake_close_forum_topic)

    result = await execute_tool(
        "archive_projet", {"name": "demo"},
        config=SimpleNamespace(config_dir=demo_config_dir),
        bot=fake_bot, chat_id=222, confirmed=True,
    )

    assert result.status == "ok"
    assert result.data["commit"] is None
    assert "push" not in calls
    assert calls["closed"] == (222, 777)


async def test_archive_projet_missing_thread_id_returns_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {"models": {}})
    _write_yaml(config_dir / "projects" / "sans-topic.yml", {
        "repo_path": str(tmp_path / "repo"),
    })

    result = await execute_tool(
        "archive_projet", {"name": "sans-topic"},
        config=SimpleNamespace(config_dir=config_dir),
        bot=SimpleNamespace(), chat_id=222, confirmed=True,
    )

    assert result.status == "error"


async def test_archive_projet_without_bot_context_returns_error(demo_config_dir: Path):
    result = await execute_tool(
        "archive_projet", {"name": "demo"},
        config=SimpleNamespace(config_dir=demo_config_dir),
        confirmed=True,
    )

    assert result.status == "error"


# --- new_feature / valider_fiche_feature (ADR 0015) ---

async def test_new_feature_without_telegram_context_returns_error():
    result = await execute_tool(
        "new_feature", {}, config=SimpleNamespace(),
    )

    assert result.status == "error"


async def test_new_feature_delegates_to_start_feature_dialogue(monkeypatch: pytest.MonkeyPatch):
    import studio.telegram.pm_dialogue as pm_dialogue_module

    calls = {}

    async def fake_start_feature_dialogue(bot, chat_id, message_thread_id, config):
        calls["args"] = (bot, chat_id, message_thread_id, config)

    monkeypatch.setattr(
        pm_dialogue_module, "start_feature_dialogue", fake_start_feature_dialogue,
    )

    config = SimpleNamespace()
    fake_bot = SimpleNamespace()
    result = await execute_tool(
        "new_feature", {}, config=config,
        bot=fake_bot, chat_id=222, message_thread_id=333,
    )

    assert result.status == "ok"
    assert calls["args"] == (fake_bot, 222, 333, config)


async def test_valider_fiche_feature_requires_confirmation():
    result = await execute_tool(
        "valider_fiche_feature", {"run_id": "run-1", "content": "# Fiche"},
        config=SimpleNamespace(),
    )

    assert result.status == "needs_confirmation"


async def test_valider_fiche_feature_writes_card(tmp_path: Path):
    config = SimpleNamespace(repo_path=tmp_path, get=lambda key, default=None: default)

    result = await execute_tool(
        "valider_fiche_feature", {"run_id": "run-042", "content": "# Ma fiche"},
        config=config, confirmed=True,
    )

    assert result.status == "ok"
    card_path = tmp_path / "specs" / "run-042" / "card-root.md"
    assert card_path.read_text(encoding="utf-8") == "# Ma fiche"
    assert result.data == {"card_root_path": "specs/run-042/card-root.md"}


# --- new_project / valider_fiche_projet (ADR 0015, phase 2) ---

async def test_new_project_without_telegram_context_returns_error():
    result = await execute_tool(
        "new_project", {}, config=SimpleNamespace(),
    )

    assert result.status == "error"


async def test_new_project_delegates_to_start_new_project_flow(
    monkeypatch: pytest.MonkeyPatch,
):
    import studio.telegram.new_project_flow as new_project_flow_module

    calls = {}

    async def fake_start_new_project_flow(bot, chat_id, config_dir):
        calls["args"] = (bot, chat_id, config_dir)

    monkeypatch.setattr(
        new_project_flow_module, "start_new_project_flow", fake_start_new_project_flow,
    )

    fake_bot = SimpleNamespace()
    result = await execute_tool(
        "new_project", {}, config=SimpleNamespace(config_dir="/config"),
        bot=fake_bot, chat_id=222,
    )

    assert result.status == "ok"
    assert calls["args"] == (fake_bot, 222, "/config")


async def test_valider_fiche_projet_requires_confirmation():
    result = await execute_tool(
        "valider_fiche_projet", {"content": "# Fiche projet"},
        config=SimpleNamespace(),
    )

    assert result.status == "needs_confirmation"


async def test_valider_fiche_projet_writes_card(tmp_path: Path):
    config = SimpleNamespace(repo_path=tmp_path, get=lambda key, default=None: default)

    result = await execute_tool(
        "valider_fiche_projet", {"content": "# Ma fiche projet"},
        config=config, confirmed=True,
    )

    assert result.status == "ok"
    card_path = tmp_path / "specs" / "fiche-projet.md"
    assert card_path.read_text(encoding="utf-8") == "# Ma fiche projet"
    assert result.data == {"fiche_projet_path": "specs/fiche-projet.md"}
