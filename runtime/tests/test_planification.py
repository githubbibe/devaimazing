"""
Tests de studio.tools.planification — support minimal de la Décision 9 de
l'ADR 0015 (table plate feature/statut/run_id/hash), nécessaire à /run
<nom_feature> (Décision 4) pour résoudre un nom de feature vers son run_id.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from studio.tools import planification
from studio.tools.planification import PlanificationEntry


def _config(repo_path: Path) -> SimpleNamespace:
    return SimpleNamespace(repo_path=repo_path, get=lambda key, default=None: default)


def test_hash_content_deterministic():
    assert planification.hash_content("abc") == planification.hash_content("abc")
    assert planification.hash_content("abc") != planification.hash_content("abd")


async def test_find_entry_returns_none_if_file_missing(tmp_path: Path):
    config = _config(tmp_path)
    assert await planification.find_entry(config, "ajout-panier") is None


async def test_upsert_entry_creates_file_from_scratch(tmp_path: Path):
    config = _config(tmp_path)

    await planification.upsert_entry(
        config,
        PlanificationEntry(
            feature_name="ajout-panier", statut="à faire",
            run_id="run-20260730-101500", content_hash="deadbeef",
        ),
    )

    content = (tmp_path / "specs" / "planification.md").read_text(encoding="utf-8")
    assert "ajout-panier" in content
    assert "run-20260730-101500" in content
    assert "deadbeef" in content

    entry = await planification.find_entry(config, "ajout-panier")
    assert entry == PlanificationEntry(
        feature_name="ajout-panier", statut="à faire",
        run_id="run-20260730-101500", content_hash="deadbeef",
    )


async def test_upsert_entry_replaces_existing_row_without_duplicate(tmp_path: Path):
    config = _config(tmp_path)
    entry = PlanificationEntry(
        feature_name="Ajout Panier", statut="à faire",
        run_id="run-1", content_hash="hash1",
    )
    await planification.upsert_entry(config, entry)

    updated = PlanificationEntry(
        feature_name="ajout   panier", statut="fait",
        run_id="run-1", content_hash="hash2",
    )
    await planification.upsert_entry(config, updated)

    content = (tmp_path / "specs" / "planification.md").read_text(encoding="utf-8")
    assert content.count("run-1") == 1

    found = await planification.find_entry(config, "AJOUT PANIER")
    assert found is not None
    assert found.statut == "fait"
    assert found.content_hash == "hash2"


async def test_find_entry_matches_despite_case_and_whitespace(tmp_path: Path):
    config = _config(tmp_path)
    await planification.upsert_entry(
        config,
        PlanificationEntry(
            feature_name="Ajout  Panier", statut="en cours",
            run_id="run-2", content_hash="hash3",
        ),
    )

    assert await planification.find_entry(config, "ajout panier") is not None
    assert await planification.find_entry(config, "  AJOUT   PANIER  ") is not None
    assert await planification.find_entry(config, "autre feature") is None


async def test_list_entries_empty_if_file_missing(tmp_path: Path):
    config = _config(tmp_path)
    assert await planification.list_entries(config) == []


async def test_list_entries_returns_all_entries_in_order(tmp_path: Path):
    config = _config(tmp_path)
    await planification.upsert_entry(
        config,
        PlanificationEntry(
            feature_name="ajout-panier", statut="à faire", run_id="run-1", content_hash="h1",
        ),
    )
    await planification.upsert_entry(
        config,
        PlanificationEntry(
            feature_name="ajout-recherche", statut="fait", run_id="run-2", content_hash="h2",
        ),
    )

    entries = await planification.list_entries(config)

    assert [e.feature_name for e in entries] == ["ajout-panier", "ajout-recherche"]
    assert entries[1].statut == "fait"


async def test_upsert_entry_stores_merged_commit(tmp_path: Path):
    config = _config(tmp_path)

    await planification.upsert_entry(
        config,
        PlanificationEntry(
            feature_name="ajout-panier", statut="fait",
            run_id="run-1", content_hash="hash1", merged_commit="abc123",
        ),
    )

    entry = await planification.find_entry(config, "ajout-panier")
    assert entry.merged_commit == "abc123"


async def test_merged_commit_defaults_to_none(tmp_path: Path):
    config = _config(tmp_path)

    await planification.upsert_entry(
        config,
        PlanificationEntry(
            feature_name="ajout-panier", statut="à faire", run_id="run-1", content_hash="hash1",
        ),
    )

    entry = await planification.find_entry(config, "ajout-panier")
    assert entry.merged_commit is None


async def test_find_entry_reads_legacy_four_column_row(tmp_path: Path):
    """Compatibilité ascendante : un specs/planification.md écrit avant
    l'introduction de merged_commit (4 colonnes) reste lisible."""
    path = tmp_path / "specs" / "planification.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# Planification\n\n## Fiches\n\n"
        "| Feature | Statut | Run ID | Hash |\n"
        "|---|---|---|---|\n"
        "| ajout-panier | fait | run-1 | hash1 |\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)

    entry = await planification.find_entry(config, "ajout-panier")

    assert entry == PlanificationEntry(
        feature_name="ajout-panier", statut="fait", run_id="run-1", content_hash="hash1",
    )


async def test_upsert_entry_preserves_other_legacy_rows(tmp_path: Path):
    """Une ligne d'une autre feature au format legacy (4 colonnes) ne doit
    pas faire planter le rendu d'une nouvelle ligne à 5 colonnes."""
    path = tmp_path / "specs" / "planification.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# Planification\n\n## Fiches\n\n"
        "| Feature | Statut | Run ID | Hash |\n"
        "|---|---|---|---|\n"
        "| ancienne-feature | fait | run-0 | hash0 |\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)

    await planification.upsert_entry(
        config,
        PlanificationEntry(
            feature_name="nouvelle-feature", statut="fait",
            run_id="run-1", content_hash="hash1", merged_commit="abc123",
        ),
    )

    ancienne = await planification.find_entry(config, "ancienne-feature")
    nouvelle = await planification.find_entry(config, "nouvelle-feature")
    assert ancienne.merged_commit is None
    assert nouvelle.merged_commit == "abc123"
