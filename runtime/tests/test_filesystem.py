"""
Tests des opérations filesystem devaimazing.
"""

import json
from pathlib import Path

import pytest

from studio.tools.filesystem import (
    append_feedback,
    inject_skills,
    parse_agent_file_blocks,
    parse_pm_structured_output,
    parse_structured_file_output,
    read_card,
    read_files,
    strip_feedback_section,
    write_card,
)
from studio.tools.tracer import RunTracer


def _events(trace_path: Path) -> list[dict]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]

CARD_WITH_FEEDBACK = """# Fiche agent - back - Run run-001

## Objectif

Faire le truc.

## Feedback

<!-- Section annotée par l'agent suivant ou l'Architecte si renvoi en arrière -->

_Aucun feedback pour l'instant._
"""


async def test_read_card_returns_content(tmp_path: Path):
    card_path = tmp_path / "card.md"
    card_path.write_text("contenu de la fiche", encoding="utf-8")

    content = await read_card(card_path)

    assert content == "contenu de la fiche"


async def test_read_card_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await read_card(tmp_path / "absent.md")


async def test_read_card_emits_card_read(tmp_path: Path):
    card_path = tmp_path / "card.md"
    card_path.write_text("contenu", encoding="utf-8")
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("back", "STUBS")

    await read_card(card_path, tracer=tracer)

    events = _events(tracer._tracer.trace_path)
    assert events[0]["event"] == "card_read"
    assert events[0]["path"] == str(card_path)


async def test_write_card_creates_parent_dirs(tmp_path: Path):
    card_path = tmp_path / "specs" / "run-001" / "back.md"

    await write_card(card_path, "# Fiche back")

    assert card_path.is_file()
    assert card_path.read_text(encoding="utf-8") == "# Fiche back"


async def test_write_card_emits_card_written(tmp_path: Path):
    card_path = tmp_path / "specs" / "run-001" / "back.md"
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("back", "STUBS")

    await write_card(card_path, "# Fiche back", tracer=tracer)

    events = _events(tracer._tracer.trace_path)
    assert events[0]["event"] == "card_written"
    assert events[0]["path"] == str(card_path)


async def test_write_card_overwrites(tmp_path: Path):
    card_path = tmp_path / "card.md"
    card_path.write_text("ancien contenu", encoding="utf-8")

    await write_card(card_path, "nouveau contenu")

    assert card_path.read_text(encoding="utf-8") == "nouveau contenu"


async def test_append_feedback_adds_entry_and_removes_placeholder(tmp_path: Path):
    card_path = tmp_path / "back.md"
    card_path.write_text(CARD_WITH_FEEDBACK, encoding="utf-8")

    await append_feedback(card_path, agent_source="front", feedback="endpoint manquant : /login")

    content = card_path.read_text(encoding="utf-8")
    assert "_Aucun feedback pour l'instant._" not in content
    assert "[front] : endpoint manquant : /login" in content
    # La section suivante (aucune ici) et le reste du fichier ne sont pas perdus
    assert "## Objectif" in content


async def test_append_feedback_keeps_previous_entries(tmp_path: Path):
    card_path = tmp_path / "back.md"
    card_path.write_text(CARD_WITH_FEEDBACK, encoding="utf-8")

    await append_feedback(card_path, agent_source="front", feedback="premier écart")
    await append_feedback(card_path, agent_source="test", feedback="second écart")

    content = card_path.read_text(encoding="utf-8")
    assert "[front] : premier écart" in content
    assert "[test] : second écart" in content


async def test_append_feedback_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await append_feedback(tmp_path / "absent.md", agent_source="front", feedback="x")


async def test_append_feedback_missing_section_raises(tmp_path: Path):
    card_path = tmp_path / "no-feedback.md"
    card_path.write_text("# Fiche sans section feedback\n\n## Objectif\n\nRien.", encoding="utf-8")

    with pytest.raises(ValueError):
        await append_feedback(card_path, agent_source="front", feedback="x")


def test_strip_feedback_section_removes_feedback_and_beyond():
    result = strip_feedback_section(CARD_WITH_FEEDBACK)
    assert "## Objectif" in result
    assert "Faire le truc." in result
    assert "## Feedback" not in result
    assert "Aucun feedback pour l'instant" not in result


def test_strip_feedback_section_no_feedback_heading_returns_unchanged():
    content = "# Fiche\n\n## Objectif\n\nFaire le truc.\n"
    assert strip_feedback_section(content) == content


async def test_read_files_includes_existing_file_content(tmp_path: Path):
    repo = tmp_path / "project"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")

    context = await read_files(repo, ["backend/main.py"])

    assert "backend/main.py" in context
    assert "from fastapi import FastAPI" in context


async def test_read_files_returns_empty_for_empty_list(tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()

    context = await read_files(repo, [])

    assert context == ""


async def test_read_files_missing_path_raises_file_not_found_error(tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()

    with pytest.raises(FileNotFoundError):
        await read_files(repo, ["backend/absent.py"])


async def test_read_files_emits_referenced_files_resolved(tmp_path: Path):
    repo = tmp_path / "project"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "main.py").write_text("x = 1\n", encoding="utf-8")
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("back", "STUBS")

    await read_files(repo, ["backend/main.py"], tracer=tracer)

    events = _events(tracer._tracer.trace_path)
    resolved = [e for e in events if e["event"] == "referenced_files_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["requested"] == ["backend/main.py"]
    assert resolved[0]["found"] == ["backend/main.py"]


async def test_read_files_missing_path_emits_error_with_missing_field(tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("back", "STUBS")

    with pytest.raises(FileNotFoundError):
        await read_files(repo, ["backend/absent.py"], tracer=tracer)

    events = _events(tracer._tracer.trace_path)
    assert events[-1]["event"] == "error"
    assert events[-1]["missing"] == "backend/absent.py"


async def test_inject_skills_appends_skill_content(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "stub-first.md").write_text("# Skill - Stub-first\n\ncontenu", encoding="utf-8")

    prompt = await inject_skills(
        base_prompt="Tu es l'agent Backend.",
        skill_names=["stub-first"],
        skills_dir=skills_dir,
    )

    assert prompt.startswith("Tu es l'agent Backend.")
    assert "# Skill - Stub-first" in prompt
    assert "contenu" in prompt


async def test_inject_skills_missing_skill_raises(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        await inject_skills(
            base_prompt="Tu es l'agent Backend.",
            skill_names=["inexistant"],
            skills_dir=skills_dir,
        )


def test_parse_agent_file_blocks_single_file():
    text = (
        'Voici le fichier :\n\n'
        '<<<DEVAIMAZING_FILE path="backend/auth/endpoints.py">>>\n'
        'def login():\n'
        '    ...\n'
        '<<<DEVAIMAZING_END>>>\n'
        '\nVoilà.'
    )

    files = parse_agent_file_blocks(text)

    assert files == {"backend/auth/endpoints.py": "def login():\n    ..."}


def test_parse_agent_file_blocks_multiple_files():
    text = (
        '<<<DEVAIMAZING_FILE path="backend/a.py">>>\n'
        'contenu a\n'
        '<<<DEVAIMAZING_END>>>\n'
        '<<<DEVAIMAZING_FILE path="backend/b.py">>>\n'
        'contenu b\n'
        '<<<DEVAIMAZING_END>>>'
    )

    files = parse_agent_file_blocks(text)

    assert files == {"backend/a.py": "contenu a", "backend/b.py": "contenu b"}


def test_parse_agent_file_blocks_no_block_raises_value_error():
    with pytest.raises(ValueError):
        parse_agent_file_blocks("Je ne peux pas produire ce fichier, contradiction détectée.")


def test_parse_agent_file_blocks_emits_parse_output_success(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("pm", "FICHES")
    text = (
        '<<<DEVAIMAZING_FILE path="backend/a.py">>>\n'
        'contenu a\n'
        '<<<DEVAIMAZING_END>>>'
    )

    parse_agent_file_blocks(text, tracer=tracer)

    events = _events(tracer._tracer.trace_path)
    assert events[0]["event"] == "parse_output"
    assert events[0]["outcome"] == "success"
    assert events[0]["files"] == ["backend/a.py"]


def test_parse_agent_file_blocks_no_block_emits_parse_output_error(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("pm", "FICHES")

    with pytest.raises(ValueError):
        parse_agent_file_blocks("contradiction détectée", tracer=tracer)

    events = _events(tracer._tracer.trace_path)
    assert events[0]["event"] == "parse_output"
    assert events[0]["outcome"] == "error"
    assert events[0]["raw_output_head"] == "contradiction détectée"


def test_parse_agent_file_blocks_last_duplicate_wins():
    text = (
        '<<<DEVAIMAZING_FILE path="backend/a.py">>>\n'
        'v1\n'
        '<<<DEVAIMAZING_END>>>\n'
        '<<<DEVAIMAZING_FILE path="backend/a.py">>>\n'
        'v2\n'
        '<<<DEVAIMAZING_END>>>'
    )

    files = parse_agent_file_blocks(text)

    assert files == {"backend/a.py": "v2"}


def test_parse_agent_file_blocks_fallback_single_fenced_block():
    text = (
        "Voici le fichier réécrit :\n\n"
        "```python\n"
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "```\n"
    )

    files = parse_agent_file_blocks(text, fallback_path="backend/main.py")

    assert files == {"backend/main.py": "from fastapi import FastAPI\napp = FastAPI()"}


def test_parse_agent_file_blocks_fallback_not_used_when_devaimazing_block_present():
    text = (
        '<<<DEVAIMAZING_FILE path="backend/a.py">>>\n'
        'v1\n'
        '<<<DEVAIMAZING_END>>>'
    )

    files = parse_agent_file_blocks(text, fallback_path="backend/other.py")

    assert files == {"backend/a.py": "v1"}


def test_parse_agent_file_blocks_fallback_ambiguous_multiple_fenced_blocks_raises():
    text = "```python\nun\n```\n\net aussi\n\n```python\ndeux\n```\n"

    with pytest.raises(ValueError):
        parse_agent_file_blocks(text, fallback_path="backend/main.py")


def test_parse_agent_file_blocks_no_fallback_path_still_raises():
    text = "```python\nsolo\n```\n"

    with pytest.raises(ValueError):
        parse_agent_file_blocks(text, fallback_path=None)


def test_parse_agent_file_blocks_absolute_path_raises():
    # Régression (2026-07-14) : Path("/repo") / "/etc/passwd" == Path("/etc/passwd")
    # en pathlib — un chemin absolu produit par l'agent contourne repo_path.
    text = (
        '<<<DEVAIMAZING_FILE path="/backend/main.py">>>\n'
        'contenu\n'
        '<<<DEVAIMAZING_END>>>'
    )

    with pytest.raises(ValueError):
        parse_agent_file_blocks(text)


def test_parse_agent_file_blocks_parent_traversal_raises():
    text = (
        '<<<DEVAIMAZING_FILE path="backend/../../etc/passwd">>>\n'
        'contenu\n'
        '<<<DEVAIMAZING_END>>>'
    )

    with pytest.raises(ValueError):
        parse_agent_file_blocks(text)


def test_parse_agent_file_blocks_fallback_absolute_path_raises():
    text = "```python\nsolo\n```\n"

    with pytest.raises(ValueError):
        parse_agent_file_blocks(text, fallback_path="/backend/main.py")


def _card_metadata(**overrides) -> dict:
    metadata = {
        "files_to_create": [], "files_to_modify": [], "files_forbidden": [],
        "existing_files_to_read": [], "dependencies": [],
    }
    metadata.update(overrides)
    return metadata


def test_parse_pm_structured_output_valid():
    structured_output = {
        "sequence": ["back", "test"],
        "cards": [
            {"agent": "back", **_card_metadata(existing_files_to_read=["backend/main.py"])},
            {"agent": "test", **_card_metadata()},
        ],
    }

    sequence, cards = parse_pm_structured_output(structured_output)

    assert sequence == ["back", "test"]
    assert cards["back"]["existing_files_to_read"] == ["backend/main.py"]
    assert cards["test"] == _card_metadata()


def test_parse_pm_structured_output_none_raises():
    with pytest.raises(ValueError):
        parse_pm_structured_output(None)


def test_parse_pm_structured_output_empty_sequence_raises():
    with pytest.raises(ValueError):
        parse_pm_structured_output({"sequence": [], "cards": []})


def test_parse_pm_structured_output_agent_missing_from_cards_raises():
    structured_output = {
        "sequence": ["back", "test"],
        "cards": [{"agent": "back", **_card_metadata()}],  # "test" manquant
    }

    with pytest.raises(ValueError):
        parse_pm_structured_output(structured_output)


def test_parse_pm_structured_output_wrong_field_type_raises():
    structured_output = {
        "sequence": ["back"],
        "cards": [{"agent": "back", **_card_metadata(existing_files_to_read="backend/main.py")}],
    }

    with pytest.raises(ValueError):
        parse_pm_structured_output(structured_output)


def test_parse_structured_file_output_single_file():
    content = (
        '{"files": [{"path": "backend/a.py", "content": "x = 1"}], "blocked_reason": ""}'
    )

    files, blocked_reason = parse_structured_file_output(content)

    assert files == {"backend/a.py": "x = 1"}
    assert blocked_reason == ""


def test_parse_structured_file_output_multiple_files():
    content = (
        '{"files": ['
        '{"path": "backend/a.py", "content": "a"}, '
        '{"path": "backend/b.py", "content": "b"}'
        '], "blocked_reason": ""}'
    )

    files, blocked_reason = parse_structured_file_output(content)

    assert files == {"backend/a.py": "a", "backend/b.py": "b"}
    assert blocked_reason == ""


def test_parse_structured_file_output_blocked():
    content = '{"files": [], "blocked_reason": "Contradiction avec le brief."}'

    files, blocked_reason = parse_structured_file_output(content)

    assert files == {}
    assert blocked_reason == "Contradiction avec le brief."


def test_parse_structured_file_output_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_structured_file_output("pas du json")


def test_parse_structured_file_output_invalid_json_emits_parse_output_error(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("back", "STUBS")

    with pytest.raises(ValueError):
        parse_structured_file_output("pas du json", tracer=tracer)

    events = _events(tracer._tracer.trace_path)
    assert events[0]["event"] == "parse_output"
    assert events[0]["outcome"] == "error"
    assert events[0]["raw_output_head"] == "pas du json"


def test_parse_structured_file_output_success_emits_parse_output_success(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1").for_agent("back", "STUBS")

    parse_structured_file_output(
        '{"files": [{"path": "backend/a.py", "content": "x = 1"}], "blocked_reason": ""}',
        tracer=tracer,
    )

    events = _events(tracer._tracer.trace_path)
    assert events[0]["event"] == "parse_output"
    assert events[0]["outcome"] == "success"
    assert events[0]["files"] == ["backend/a.py"]


def test_parse_structured_file_output_missing_fields_raises():
    with pytest.raises(ValueError):
        parse_structured_file_output('{"files": []}')  # blocked_reason absent


def test_parse_structured_file_output_incomplete_file_entry_raises():
    with pytest.raises(ValueError):
        parse_structured_file_output(
            '{"files": [{"path": "backend/a.py"}], "blocked_reason": ""}'
        )  # content absent


def test_parse_structured_file_output_absolute_path_raises():
    # Régression (2026-07-14, run réel) : qwen2.5:1.5b-instruct a produit
    # "path": "/backend/main.py" (imitation littérale de "/backend/" dans
    # prompts/backend.md) — Path("/repo") / "/backend/main.py" ignore
    # silencieusement repo_path, écriture tentée hors du repo cible.
    content = '{"files": [{"path": "/backend/main.py", "content": "x = 1"}], "blocked_reason": ""}'

    with pytest.raises(ValueError):
        parse_structured_file_output(content)


def test_parse_structured_file_output_parent_traversal_raises():
    content = (
        '{"files": [{"path": "backend/../../etc/passwd", "content": "x"}], '
        '"blocked_reason": ""}'
    )

    with pytest.raises(ValueError):
        parse_structured_file_output(content)
