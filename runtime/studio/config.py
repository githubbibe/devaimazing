"""
Chargement de la configuration devaimazing.

Charge studio.yml (config globale) et le fichier projet cible.
Les valeurs projet écrasent les valeurs globales si définies.
"""

import os
from pathlib import Path
from typing import Any, Optional

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Fusionne récursivement `override` dans une copie de `base`.

    Une clé dont la valeur est un mapping dans les deux dictionnaires est
    fusionnée récursivement (les sous-clés non redéfinies par `override`
    sont conservées). Toute autre clé de `override` remplace ou ajoute la
    valeur correspondante dans `base`.
    """
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class StudioConfig:
    """
    Configuration complète du studio pour un run donné.

    Charge studio.yml, le fichier projet, puis local.yml s'il existe, et
    fusionne les trois dans cet ordre (chaque niveau peut écraser
    n'importe quelle valeur du précédent).
    """

    def __init__(self, project_name: str, config_dir: Optional[Path] = None):
        """
        Args:
            project_name: Nom du projet tel que défini dans config/projects/<nom>.yml
            config_dir: Répertoire de config (défaut: répertoire du package devaimazing)

        Raises:
            FileNotFoundError: Si studio.yml ou le fichier projet est introuvable.
            ValueError: Si le fichier projet ou local.yml est invalide.
        """
        self._project_name = project_name
        self._config_dir = (
            Path(config_dir) if config_dir is not None
            else Path(__file__).resolve().parents[2] / "config"
        )

        studio_yml_path = self._config_dir / "studio.yml"
        if not studio_yml_path.is_file():
            raise FileNotFoundError(f"Configuration globale introuvable : {studio_yml_path}")

        project_yml_path = self._config_dir / "projects" / f"{project_name}.yml"
        if not project_yml_path.is_file():
            raise FileNotFoundError(f"Configuration projet introuvable : {project_yml_path}")

        with studio_yml_path.open("r", encoding="utf-8") as f:
            global_config = yaml.safe_load(f) or {}
        with project_yml_path.open("r", encoding="utf-8") as f:
            project_config = yaml.safe_load(f) or {}

        if not isinstance(global_config, dict):
            raise ValueError(f"Configuration globale invalide (mapping attendu) : {studio_yml_path}")
        if not isinstance(project_config, dict):
            raise ValueError(f"Configuration projet invalide (mapping attendu) : {project_yml_path}")

        merged = _deep_merge(global_config, project_config)

        # local.yml : override local optionnel, gitignoré, jamais commité.
        # Pour les valeurs qui ne doivent jamais apparaître dans l'historique
        # git d'un dépôt public (ex. notifications.ntfy.topic — sa sécurité
        # repose entièrement sur le fait qu'il reste secret, voir
        # docs/roadmap.md). Absent par défaut, ne casse rien si non créé.
        local_yml_path = self._config_dir / "local.yml"
        if local_yml_path.is_file():
            with local_yml_path.open("r", encoding="utf-8") as f:
                local_config = yaml.safe_load(f) or {}
            if not isinstance(local_config, dict):
                raise ValueError(f"Configuration locale invalide (mapping attendu) : {local_yml_path}")
            merged = _deep_merge(merged, local_config)

        self._raw = merged

    @property
    def repo_path(self) -> Path:
        """Chemin absolu vers le repo du projet cible."""
        raw = self._raw.get("repo_path")
        if not raw:
            raise ValueError(
                f"repo_path manquant dans la configuration du projet '{self._project_name}'"
            )
        return Path(raw).expanduser()

    @property
    def project_name(self) -> str:
        """Nom du projet."""
        return self._project_name

    @property
    def models(self) -> dict[str, str]:
        """Mapping nom_modèle -> identifiant LLM."""
        return dict(self._raw.get("models", {}))

    @property
    def checkpoints(self) -> dict[str, bool]:
        """Mapping phase -> checkpoint activé."""
        return dict(self._raw.get("checkpoints", {}))

    @property
    def ollama_base_url(self) -> str:
        """URL de base de l'API Ollama."""
        return self._raw.get("ollama", {}).get("base_url", "http://localhost:11434")

    @property
    def metrics_db_path(self) -> Path:
        """Chemin vers metrics.db."""
        raw = self._raw.get("metrics", {}).get("db_path", "~/.devaimazing/metrics.db")
        return Path(raw).expanduser()

    @property
    def state_db_path(self) -> Path:
        """Chemin vers state.db (checkpointer LangGraph)."""
        raw = self._raw.get("state", {}).get("db_path", "~/.devaimazing/state.db")
        return Path(raw).expanduser()

    @property
    def project_constraints(self) -> dict[str, Any]:
        """Contraintes projet transmises à l'Architecte."""
        return dict(self._raw.get("project_constraints", {}))

    @property
    def test_command(self) -> Optional[str]:
        """
        Commande d'exécution de la suite de tests du projet cible (phase 7,
        voir config/projects/<nom>.yml section `test`). Contient le
        placeholder `{target_dir}`, substitué par repo_path à l'exécution
        (voir studio.nodes.test). None si le projet n'a pas défini cette
        section — décision volontairement par projet, pas de commande
        globale par défaut, les stacks cibles étant hétérogènes.
        """
        return self._raw.get("test", {}).get("command")

    def get(self, key: str, default: Any = None) -> Any:
        """Accès générique à une clé de config."""
        return self._raw.get(key, default)

    @classmethod
    def from_env(cls) -> "StudioConfig":
        """
        Crée une config depuis les variables d'environnement.

        Variables attendues :
            DEVAIMAZING_PROJECT: Nom du projet
            DEVAIMAZING_CONFIG_DIR: Répertoire de config (optionnel)

        Raises:
            ValueError: Si DEVAIMAZING_PROJECT n'est pas défini.
        """
        project_name = os.environ.get("DEVAIMAZING_PROJECT")
        if not project_name:
            raise ValueError("Variable d'environnement DEVAIMAZING_PROJECT non définie")

        config_dir_raw = os.environ.get("DEVAIMAZING_CONFIG_DIR")
        config_dir = Path(config_dir_raw).expanduser() if config_dir_raw else None

        return cls(project_name=project_name, config_dir=config_dir)
