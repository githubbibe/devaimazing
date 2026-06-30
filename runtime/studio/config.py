"""
Chargement de la configuration devaimazing.

Charge studio.yml (config globale) et le fichier projet cible.
Les valeurs projet écrasent les valeurs globales si définies.
"""

from pathlib import Path
from typing import Any, Optional
import yaml


class StudioConfig:
    """
    Configuration complète du studio pour un run donné.
    
    Charge studio.yml puis le fichier projet et fusionne les deux.
    Le fichier projet peut écraser n'importe quelle valeur de studio.yml.
    """

    def __init__(self, project_name: str, config_dir: Optional[Path] = None):
        """
        Args:
            project_name: Nom du projet tel que défini dans config/projects/<nom>.yml
            config_dir: Répertoire de config (défaut: répertoire du package devaimazing)

        Raises:
            FileNotFoundError: Si studio.yml ou le fichier projet est introuvable.
            ValueError: Si le fichier projet est invalide.
        """
        ...

    @property
    def repo_path(self) -> Path:
        """Chemin absolu vers le repo du projet cible."""
        ...

    @property
    def project_name(self) -> str:
        """Nom du projet."""
        ...

    @property
    def models(self) -> dict[str, str]:
        """Mapping nom_modèle -> identifiant LLM."""
        ...

    @property
    def checkpoints(self) -> dict[str, bool]:
        """Mapping phase -> checkpoint activé."""
        ...

    @property
    def ollama_base_url(self) -> str:
        """URL de base de l'API Ollama."""
        ...

    @property
    def metrics_db_path(self) -> Path:
        """Chemin vers metrics.db."""
        ...

    @property
    def state_db_path(self) -> Path:
        """Chemin vers state.db (checkpointer LangGraph)."""
        ...

    @property
    def project_constraints(self) -> dict[str, Any]:
        """Contraintes projet transmises à l'Architecte."""
        ...

    def get(self, key: str, default: Any = None) -> Any:
        """Accès générique à une clé de config."""
        ...

    @classmethod
    def from_env(cls) -> "StudioConfig":
        """
        Crée une config depuis les variables d'environnement.
        
        Variables attendues :
            DEVAIMAZING_PROJECT: Nom du projet
            DEVAIMAZING_CONFIG_DIR: Répertoire de config (optionnel)
        """
        ...
