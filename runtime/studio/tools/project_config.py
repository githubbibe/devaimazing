"""
Écriture ciblée de config/projects/<nom>.yml.

Avant cette tranche, la seule fonction d'écriture existante
(studio.cli._write_project_config) génère un fichier neuf depuis un
template — aucune fonction ne modifiait un fichier projet déjà existant.
set_project_thread_id ajoute ou met à jour uniquement la clé
telegram.thread_id, par édition de texte ciblée plutôt que par un
round-trip YAML complet (yaml.safe_dump perdrait tous les commentaires
existants, abondants dans ces fichiers, voir ADR 0013).
"""

import re
from pathlib import Path

import yaml

# Ne cible que le bloc généré par cette fonction elle-même (telegram: suivi
# immédiatement de sa seule clé thread_id) — c'est le seul writer de ce bloc,
# pas la peine de gérer une structure telegram: arbitraire écrite à la main.
_TELEGRAM_BLOCK_PATTERN = re.compile(
    r"(?m)^telegram:\n[ \t]+thread_id:[ \t]*(?P<value>\S+)[ \t]*$"
)


async def set_project_thread_id(config_path: Path, thread_id: int) -> None:
    """
    Ajoute ou met à jour la clé telegram.thread_id dans un fichier projet.

    Args:
        config_path: Chemin vers config/projects/<nom>.yml (doit exister).
        thread_id: Identifiant du topic Telegram Forum associé au projet.

    Raises:
        FileNotFoundError: Si config_path n'existe pas.
        RuntimeError: Si le résultat n'est pas un YAML valide portant la
            bonne valeur — le fichier original n'est alors pas modifié.

    Side effects:
        Réécrit config_path sur disque si la validation réussit.
    """
    if not config_path.is_file():
        raise FileNotFoundError(f"Fichier projet introuvable : {config_path}")

    original = config_path.read_text(encoding="utf-8")
    match = _TELEGRAM_BLOCK_PATTERN.search(original)

    if match:
        updated = (
            original[: match.start("value")] + str(thread_id) + original[match.end("value") :]
        )
    else:
        separator = "" if original.endswith("\n") else "\n"
        block = (
            f"{separator}\n"
            "# Ajouté automatiquement lors de la création du topic Telegram\n"
            "# associé à ce projet (ADR 0013).\n"
            "telegram:\n"
            f"  thread_id: {thread_id}\n"
        )
        updated = original + block

    parsed = yaml.safe_load(updated)
    if not isinstance(parsed, dict) or parsed.get("telegram", {}).get("thread_id") != thread_id:
        raise RuntimeError(
            f"Écriture de telegram.thread_id invalide pour {config_path} — "
            "fichier non modifié."
        )

    config_path.write_text(updated, encoding="utf-8")
