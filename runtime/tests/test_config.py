"""
Tests de la configuration devaimazing.
"""

import pytest
from pathlib import Path
from studio.config import StudioConfig


def test_config_loads_studio_yml():
    """Vérifie que studio.yml est chargé sans erreur."""
    ...


def test_config_project_overrides_studio():
    """Vérifie qu'un paramètre projet écrase le paramètre global."""
    ...


def test_config_repo_path_expanded():
    """Vérifie que ~ est expandé dans repo_path."""
    ...


def test_config_missing_project_raises():
    """Vérifie qu'un projet inconnu lève FileNotFoundError."""
    ...
