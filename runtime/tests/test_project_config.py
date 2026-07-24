"""
Tests de studio.tools.project_config.

Utilise de vrais fichiers YAML commentés dans tmp_path (pas de mock) : ce
qu'on vérifie précisément est que les commentaires existants survivent à
l'édition, ce qu'un mock ne pourrait pas exercer.
"""

from pathlib import Path

import pytest
import yaml
from studio.tools.project_config import set_project_thread_id


@pytest.fixture
def project_yml(tmp_path: Path) -> Path:
    path = tmp_path / "demo.yml"
    path.write_text(
        "# Config du projet demo\n"
        "name: demo\n"
        "repo_path: ~/code/demo\n"
        "\n"
        "# Contraintes transmises à l'Architecte\n"
        "project_constraints:\n"
        "  language: python\n",
        encoding="utf-8",
    )
    return path


async def test_set_project_thread_id_appends_block_preserving_comments(project_yml: Path):
    original = project_yml.read_text(encoding="utf-8")

    await set_project_thread_id(project_yml, thread_id=12345)

    updated = project_yml.read_text(encoding="utf-8")
    assert original in updated
    assert "# Config du projet demo" in updated
    assert "# Contraintes transmises à l'Architecte" in updated

    parsed = yaml.safe_load(updated)
    assert parsed["telegram"]["thread_id"] == 12345


async def test_set_project_thread_id_updates_existing_value(project_yml: Path):
    await set_project_thread_id(project_yml, thread_id=111)
    await set_project_thread_id(project_yml, thread_id=222)

    updated = project_yml.read_text(encoding="utf-8")
    parsed = yaml.safe_load(updated)
    assert parsed["telegram"]["thread_id"] == 222
    # Un seul bloc telegram, pas un doublon ajouté à chaque appel.
    assert updated.count("telegram:") == 1


async def test_set_project_thread_id_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await set_project_thread_id(tmp_path / "inexistant.yml", thread_id=1)
