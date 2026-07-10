"""
Tests des opérations filesystem devaimazing.
"""

from pathlib import Path

import pytest

from studio.tools.filesystem import append_feedback, inject_skills, read_card, write_card

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


async def test_write_card_creates_parent_dirs(tmp_path: Path):
    card_path = tmp_path / "specs" / "run-001" / "back.md"

    await write_card(card_path, "# Fiche back")

    assert card_path.is_file()
    assert card_path.read_text(encoding="utf-8") == "# Fiche back"


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
