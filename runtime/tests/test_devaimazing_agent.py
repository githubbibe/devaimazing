"""
Tests de studio.devaimazing.agent (ADR 0013, tranche S4).

N'appelle jamais un vrai serveur Ollama : studio.devaimazing.agent.run_ollama
est monkeypatché par un faux appel scripté (voir test_ollama.py pour le même
principe appliqué au wrapper lui-même).
"""

from types import SimpleNamespace

import pytest
import studio.devaimazing.agent as agent_module
import studio.tools.registry as registry_module
from studio.devaimazing.agent import (
    build_system_prompt,
    parse_devaimazing_turn,
    run_devaimazing_turn,
)
from studio.tools.registry import ToolSpec


def test_build_system_prompt_lists_every_registry_tool():
    prompt = build_system_prompt()
    for name in registry_module.TOOL_REGISTRY:
        assert f'"{name}"' in prompt


def test_parse_devaimazing_turn_with_tool_call():
    reply, tool_call = parse_devaimazing_turn(
        '{"reply": "", "tool_call": {"name": "lire_statut", "arguments": {"run_id": "r1"}}}'
    )
    assert reply == ""
    assert tool_call == {"name": "lire_statut", "arguments": {"run_id": "r1"}}


def test_parse_devaimazing_turn_without_tool_call():
    reply, tool_call = parse_devaimazing_turn('{"reply": "Bonjour !", "tool_call": null}')
    assert reply == "Bonjour !"
    assert tool_call is None


def test_parse_devaimazing_turn_invalid_json():
    with pytest.raises(ValueError):
        parse_devaimazing_turn("pas du json")


def test_parse_devaimazing_turn_missing_fields():
    with pytest.raises(ValueError):
        parse_devaimazing_turn('{"reply": "salut"}')


def test_parse_devaimazing_turn_tool_call_arguments_not_a_dict():
    with pytest.raises(ValueError):
        parse_devaimazing_turn(
            '{"reply": "", "tool_call": {"name": "lire_statut", "arguments": "r1"}}'
        )


def _fake_run_ollama(content: str):
    async def _run_ollama(**_kwargs):
        return {"content": content, "tokens_prompt": 1, "tokens_completion": 1, "duration_ms": 0}

    return _run_ollama


async def test_run_devaimazing_turn_no_tool_call_returns_reply(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_module, "run_ollama", _fake_run_ollama('{"reply": "Bonjour !", "tool_call": null}')
    )

    text, pending = await run_devaimazing_turn(
        "Bonjour", config=SimpleNamespace(), model="gemma3:4b",
    )

    assert text == "Bonjour !"
    assert pending is None


async def test_run_devaimazing_turn_tool_call_ignores_reply(monkeypatch: pytest.MonkeyPatch):
    async def handler(config, **_kwargs):
        return {"projects": ["alpha", "beta"]}

    spec = ToolSpec(
        name="lister_projets", description="x", parameters={"type": "object", "properties": {}},
        destructif=False, requiert_confirmation=False, sauvegarde_avant=False, handler=handler,
    )
    monkeypatch.setitem(registry_module.TOOL_REGISTRY, "lister_projets", spec)
    monkeypatch.setattr(
        agent_module, "run_ollama",
        _fake_run_ollama(
            '{"reply": "des noms inventes", "tool_call": {"name": "lister_projets", '
            '"arguments": {}}}'
        ),
    )

    text, pending = await run_devaimazing_turn(
        "Liste les projets", config=SimpleNamespace(), model="gemma3:4b",
    )

    assert "des noms inventes" not in text
    assert "projects" in text
    assert pending is None


async def test_run_devaimazing_turn_needs_confirmation(monkeypatch: pytest.MonkeyPatch):
    async def handler(config, **_kwargs):
        raise AssertionError("le handler ne doit pas être appelé sans confirmation")

    spec = ToolSpec(
        name="archive_projet", description="x",
        parameters={
            "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"],
        },
        destructif=True, requiert_confirmation=True, sauvegarde_avant=True, handler=handler,
    )
    monkeypatch.setitem(registry_module.TOOL_REGISTRY, "archive_projet", spec)
    monkeypatch.setattr(
        agent_module, "run_ollama",
        _fake_run_ollama(
            '{"reply": "", "tool_call": {"name": "archive_projet", "arguments": {"name": "x"}}}'
        ),
    )

    text, pending = await run_devaimazing_turn(
        "Archive le projet x", config=SimpleNamespace(), model="gemma3:4b",
    )

    assert pending == ("archive_projet", {"name": "x"})
    assert "Confirmer" in text


async def test_run_devaimazing_turn_near_miss_tool_name_never_calls_real_handler(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Nom d'outil proche mais halluciné (ex. "lire_projets" au lieu de
    "lister_projets", observé en pratique le 2026-07-29 — voir
    docs/roadmap.md) : le vrai handler ne doit jamais être atteint, même par
    coïncidence de préfixe/similarité, seul un nom EXACT compte.
    """
    async def handler(config, **_kwargs):
        raise AssertionError("le handler réel ne doit jamais être appelé sur un nom approché")

    spec = ToolSpec(
        name="lister_projets", description="x", parameters={"type": "object", "properties": {}},
        destructif=False, requiert_confirmation=False, sauvegarde_avant=False, handler=handler,
    )
    monkeypatch.setitem(registry_module.TOOL_REGISTRY, "lister_projets", spec)
    monkeypatch.setattr(
        agent_module, "run_ollama",
        _fake_run_ollama('{"reply": "", "tool_call": {"name": "lire_projets", "arguments": {}}}'),
    )

    text, pending = await run_devaimazing_turn(
        "Fais un truc", config=SimpleNamespace(), model="gemma3:4b",
    )

    assert pending is None
    assert "lire_projets" in text or "inconnu" in text.lower()


async def test_run_devaimazing_turn_unparseable_output_falls_back(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_module, "run_ollama", _fake_run_ollama("pas du json du tout"))

    text, pending = await run_devaimazing_turn(
        "Bonjour", config=SimpleNamespace(), model="gemma3:4b",
    )

    assert pending is None
    assert "reformuler" in text.lower()
