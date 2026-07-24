"""
Tests de studio.tools.registry (ADR 0013, Décision 4).
"""

from types import SimpleNamespace

import pytest
import studio.tools.queries as queries_module
import studio.tools.registry as registry_module
from studio.tools.registry import (
    TOOL_REGISTRY,
    ToolSpec,
    execute_tool,
    parse_slash_command,
    to_ollama_tool,
)

# Table de l'ADR 0013, Décision 4 — un des points les plus structurants de
# l'ADR, vérifié explicitement outil par outil pour ne pas dériver en
# silence d'une future édition de registry.py.
_ADR_TABLE = {
    "lire_statut": (False, False, False),
    "lire_progression": (False, False, False),
    "lister_projets": (False, False, False),
    "creer_projet": (False, False, False),
    "archive_projet": (True, True, True),
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
    assert result.data == {"echo": {"run_id": "run-1"}}


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
        "creer_projet", {"name": "x"}, config=SimpleNamespace(), confirmed=False
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
