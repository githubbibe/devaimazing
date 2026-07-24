"""
Résolution topic Telegram <-> projet (ADR 0013, Décision 2 et 3).

Un topic Telegram Forum (message_thread_id) est associé à un projet via la
clé telegram.thread_id de son config/projects/<nom>.yml (écrite par
studio.tools.project_config.set_project_thread_id, tranche S3 — pas encore
appelée). Ce module scanne ces fichiers pour construire la correspondance
dans l'autre sens, utilisée par le bot pour savoir quel projet un message
Telegram concerne.
"""

from pathlib import Path
from typing import Optional

import yaml


def load_topic_map(config_dir: Path) -> dict[int, str]:
    """
    Associe chaque thread_id Telegram connu au nom du projet correspondant.

    Args:
        config_dir: Répertoire de config (voir StudioConfig.config_dir).

    Returns:
        Mapping thread_id -> nom de projet. Un projet sans telegram.thread_id
        configuré (pas encore créé via creer_projet, ou config antérieure à
        l'ADR 0013) n'apparaît simplement pas dans le mapping.
    """
    projects_dir = config_dir / "projects"
    topic_map: dict[int, str] = {}
    if not projects_dir.is_dir():
        return topic_map

    for project_yml in sorted(projects_dir.glob("*.yml")):
        data = yaml.safe_load(project_yml.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        thread_id = data.get("telegram", {}).get("thread_id")
        if thread_id is not None:
            topic_map[int(thread_id)] = project_yml.stem

    return topic_map


def resolve_project(message_thread_id: Optional[int], topic_map: dict[int, str]) -> Optional[str]:
    """
    Résout le nom de projet associé à un message Telegram.

    Args:
        message_thread_id: message_thread_id du message reçu (None : topic
            General, ou groupe sans topics activés).
        topic_map: Mapping produit par load_topic_map.

    Returns:
        Nom du projet, ou None si le message vient de General ou d'un topic
        non associé à un projet connu — pas une erreur, un message dans
        General ne concerne pas forcément un projet (voir ADR 0013, point 6
        de la Décision 3, transfert General -> projet, tranche S5).
    """
    if message_thread_id is None:
        return None
    return topic_map.get(message_thread_id)
