"""
specs/planification.md — table minimale (feature, statut, run_id, hash de
contenu de sa fiche) permettant à `/run <nom_feature>` (ADR 0015, Décision 4)
de résoudre un nom de feature vers son run_id et de détecter si sa fiche a
changé depuis le dernier run.

Version réduite de la Décision 9 de l'ADR 0015 : une table plate, upsertée
une ligne à la fois. Le regroupement par sous-phases et la section de
raisonnement (réévaluation complète du fichier par le PM à chaque nouvelle
fiche) restent hors périmètre — ce module ne touche jamais le contenu situé
après la section `## Fiches`, pour rester compatible avec un futur
enrichissement de cette nature.

Hash de CONTENU (sha256 du texte de card-root.md), pas hash de commit Git :
au moment où `/run` doit décider, la fiche n'a encore jamais été commitée
(le commit de card-root.md n'a lieu que bien plus tard dans le pipeline, à
la création de la branche du run — voir studio.nodes.pm._create_branch_and_advance,
fin de Phase.FICHES) — un hash de commit n'est donc pas disponible au bon
moment. Écart pragmatique par rapport au texte de l'ADR, à documenter en
addendum.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from studio.config import StudioConfig

_HEADING = "## Fiches"
_TABLE_HEADER = "| Feature | Statut | Run ID | Hash | Commit fusionné |"
_TABLE_SEPARATOR = "|---|---|---|---|---|"

_WHITESPACE_PATTERN = re.compile(r"\s+")


def hash_content(content: str) -> str:
    """Hash de contenu (sha256 hex) — voir docstring module pour le choix
    délibéré d'un hash de contenu plutôt que d'un hash de commit Git."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlanificationEntry:
    """
    Une ligne de specs/planification.md.

    Args:
        feature_name: Nom de la feature tel qu'extrait de card-root.md
            (voir studio.nodes.pm.extract_feature_name) — stocké tel quel,
            pas normalisé (lisibilité du fichier), voir _normalize pour le
            matching.
        statut: "à faire" | "en cours" | "fait" — projection best-effort du
            statut réel du run (voir studio.telegram.run_flow), pas un
            miroir exact de studio.state.RunStatus.
        run_id: Identifiant du run associé à cette feature, fixé une seule
            fois à la validation de /new_feature (voir
            studio.telegram.pm_dialogue._generate_run_id) — jamais régénéré
            par ce module.
        content_hash: hash_content(card-root.md) au moment du dernier
            upsert (validation de la fiche, ou fin de run réussi).
        merged_commit: Hash du commit de merge vers git.base_branch (voir
            studio.nodes.closer, studio.tools.git.merge_run_branch) — hash
            Git réel, contrairement à content_hash (voir docstring module).
            Renseigné seulement après un run terminé avec succès (statut
            "fait") ; None sinon, y compris pour une ligne écrite avant
            l'introduction de ce champ (compatibilité ascendante, voir
            _normalize_row) — traçabilité de la version de code livrée pour
            cette feature, utile pour une future reprise/édition d'une
            feature déjà exécutée.
    """

    feature_name: str
    statut: str
    run_id: str
    content_hash: str
    merged_commit: Optional[str] = None


def _normalize(feature_name: str) -> str:
    """Casse et espaces insensibles pour le matching (voir find_entry) —
    le nom stocké dans le fichier reste tel qu'extrait de card-root.md."""
    return _WHITESPACE_PATTERN.sub(" ", feature_name.strip()).lower()


def _normalize_row(cells: list[str]) -> Optional[list[str]]:
    """
    Complète une ligne à 4 cellules (ancien format, avant l'ajout de
    merged_commit) avec une 5e cellule vide — compatibilité ascendante avec
    un specs/planification.md existant écrit par une version antérieure.

    Returns:
        Toujours 5 cellules si reconnu. None si le nombre de cellules ne
        correspond à aucun format connu (ligne malformée, ignorée comme
        avant l'introduction de ce champ).
    """
    if len(cells) == 5:
        return cells
    if len(cells) == 4:
        return cells + [""]
    return None


def _specs_dir(config: StudioConfig) -> str:
    return config.get("structure", {}).get("specs_dir", "specs/")


def _planification_path(config: StudioConfig) -> Path:
    return config.repo_path / _specs_dir(config) / "planification.md"


def _parse_rows(content: str) -> list[list[str]]:
    """
    Parse les lignes de données de la section "## Fiches" — même motif que
    studio.tools.queries.parse_run_history_table (heading, séparateur
    `|---`, jusqu'au prochain `## `), réutilisé ici pour ne pas dupliquer ce
    style de parsing de table markdown.
    """
    heading_index = content.find(_HEADING)
    if heading_index == -1:
        return []
    separator_index = content.find("\n|---", heading_index)
    if separator_index == -1:
        return []
    next_heading_index = content.find("\n## ", separator_index)
    section_end = next_heading_index if next_heading_index != -1 else len(content)
    section = content[separator_index:section_end]

    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        is_separator_row = all(set(cell) <= {"-"} for cell in cells)
        if is_separator_row or not any(cells):
            continue
        rows.append(cells)
    return rows


def _render(rows: list[list[str]]) -> str:
    lines = ["# Planification", "", _HEADING, "", _TABLE_HEADER, _TABLE_SEPARATOR]
    for feature_name, statut, run_id, content_hash, merged_commit in rows:
        lines.append(
            f"| {feature_name} | {statut} | {run_id} | {content_hash} | {merged_commit} |"
        )
    lines.append("")
    return "\n".join(lines)


async def find_entry(config: StudioConfig, feature_name: str) -> Optional[PlanificationEntry]:
    """
    Cherche une entrée par nom de feature (casse/espaces insensibles).

    Returns:
        None si specs/planification.md n'existe pas encore, ou si aucune
        ligne ne correspond à feature_name.
    """
    path = _planification_path(config)
    if not path.is_file():
        return None

    target = _normalize(feature_name)
    for raw_cells in _parse_rows(path.read_text(encoding="utf-8")):
        cells = _normalize_row(raw_cells)
        if cells is None:
            continue
        row_feature_name, statut, run_id, content_hash, merged_commit = cells
        if _normalize(row_feature_name) == target:
            return PlanificationEntry(
                feature_name=row_feature_name, statut=statut,
                run_id=run_id, content_hash=content_hash,
                merged_commit=merged_commit or None,
            )
    return None


async def list_entries(config: StudioConfig) -> list[PlanificationEntry]:
    """
    Toutes les entrées de specs/planification.md, dans l'ordre du fichier —
    utilisé par l'écran « Lancer une feature » du menu à boutons (ADR 0015,
    Décision 7, studio.telegram.menu).

    Returns:
        Liste vide si specs/planification.md n'existe pas encore.
    """
    path = _planification_path(config)
    if not path.is_file():
        return []

    entries = []
    for raw_cells in _parse_rows(path.read_text(encoding="utf-8")):
        cells = _normalize_row(raw_cells)
        if cells is None:
            continue
        feature_name, statut, run_id, content_hash, merged_commit = cells
        entries.append(PlanificationEntry(
            feature_name=feature_name, statut=statut, run_id=run_id, content_hash=content_hash,
            merged_commit=merged_commit or None,
        ))
    return entries


async def upsert_entry(config: StudioConfig, entry: PlanificationEntry) -> None:
    """
    Remplace la ligne existante pour entry.feature_name (matching casse/
    espaces insensible, voir _normalize), ou l'ajoute en fin de table.

    Side effects:
        Crée specs/planification.md (et son répertoire parent) s'il
        n'existe pas encore, avec un gabarit minimal codé en dur (aucun
        template externe : pas de placeholder à substituer).
    """
    path = _planification_path(config)
    raw_rows = _parse_rows(path.read_text(encoding="utf-8")) if path.is_file() else []
    rows = [r for r in (_normalize_row(cells) for cells in raw_rows) if r is not None]

    target = _normalize(entry.feature_name)
    new_row = [
        entry.feature_name, entry.statut, entry.run_id, entry.content_hash,
        entry.merged_commit or "",
    ]
    replaced = False
    for index, cells in enumerate(rows):
        if _normalize(cells[0]) == target:
            rows[index] = new_row
            replaced = True
            break
    if not replaced:
        rows.append(new_row)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(rows), encoding="utf-8")
