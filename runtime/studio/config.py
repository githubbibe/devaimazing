"""
Chargement de la configuration devaimazing.

Charge studio.yml (config globale) et le fichier projet cible.
Les valeurs projet écrasent les valeurs globales si définies.
"""

import contextlib
import contextvars
import os
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml

# Alternative à DEVAIMAZING_PROJECT/DEVAIMAZING_CONFIG_DIR (os.environ, donc
# process-wide) pour les appelants qui exécutent plusieurs projets en
# parallèle dans le même process — le bot Telegram (ADR 0013) sert plusieurs
# topics-projets à la fois, chacun pouvant avoir un run actif en même temps
# (studio.telegram.run_flow). Un ContextVar est isolé par tâche asyncio :
# chaque asyncio.Task capture sa propre copie du contexte à sa création, donc
# project_context() posé dans une tâche n'affecte jamais les autres tâches en
# cours — contrairement à os.environ qui aurait pu être écrasé par un autre
# run pendant un simple `await` (trouvé en run réel, voir docs/roadmap.md).
# from_env() reste la seule porte d'entrée des nodes vers la config (aucun
# changement de signature chez eux) ; elle regarde d'abord ce ContextVar,
# et ne retombe sur os.environ que s'il est vide (chemin CLI, un seul projet
# par process, inchangé).
_current_project_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "devaimazing_current_project_name", default=None
)
_current_config_dir: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "devaimazing_current_config_dir", default=None
)


@contextlib.contextmanager
def project_context(project_name: str, config_dir: Optional[Path] = None) -> Iterator[None]:
    """
    Positionne project_name/config_dir pour la durée du bloc, via ContextVar
    plutôt que os.environ (voir commentaire de module) — à englober autour de
    tout code appelant StudioConfig.from_env() indirectement (ex. les nodes
    du graphe LangGraph, via graph.astream) quand plusieurs projets peuvent
    tourner en même temps dans le process courant.

    Args:
        project_name: Nom du projet à exposer à from_env() pour ce bloc.
        config_dir: Répertoire de config à exposer (optionnel, comme
            DEVAIMAZING_CONFIG_DIR).
    """
    token_project = _current_project_name.set(project_name)
    token_dir = _current_config_dir.set(str(config_dir) if config_dir is not None else None)
    try:
        yield
    finally:
        _current_project_name.reset(token_project)
        _current_config_dir.reset(token_dir)


def default_config_dir() -> Path:
    """Répertoire config/ par défaut (racine du dépôt devaimazing)."""
    return Path(__file__).resolve().parents[2] / "config"


def _read_yaml_mapping(path: Path, *, required: bool, label: str) -> dict:
    """Charge un fichier YAML en dict, valide que c'est bien un mapping."""
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"{label} introuvable : {path}")
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{label} invalide (mapping attendu) : {path}")
    return data


def load_global_telegram_config(config_dir: Optional[Path] = None) -> dict:
    """
    Section telegram: de studio.yml, avec override local.yml appliqué.

    Distinct de StudioConfig : le bot Telegram (ADR 0013) sert plusieurs
    projets à la fois (un topic = un projet, voir Décision 2), il n'a donc
    pas de project_name unique à charger au démarrage — seulement la
    configuration transverse (token, chat_id autorisé). N'expose que la
    section telegram:, pas toute la config globale (pas de besoin identifié
    au-delà pour l'instant).

    Args:
        config_dir: Répertoire de config (défaut : répertoire du package devaimazing).

    Returns:
        Contenu de la clé telegram: (dict vide si absente).

    Raises:
        FileNotFoundError: Si studio.yml est introuvable.
        ValueError: Si studio.yml ou local.yml n'est pas un mapping YAML valide.
    """
    resolved_config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
    global_config = _read_yaml_mapping(
        resolved_config_dir / "studio.yml", required=True, label="Configuration globale"
    )
    local_config = _read_yaml_mapping(
        resolved_config_dir / "local.yml", required=False, label="Configuration locale"
    )
    merged = _deep_merge(global_config, local_config)
    return dict(merged.get("telegram", {}))


def load_global_devaimazing_config(config_dir: Optional[Path] = None) -> dict:
    """
    Paramètres LLM globaux de l'agent Devaimazing (ADR 0013, tranche S4).

    Distinct de StudioConfig pour la même raison que
    load_global_telegram_config : Devaimazing n'est scopé à aucun projet
    tant que le tour de conversation n'a pas déterminé quel outil (donc quel
    projet) est concerné — voir studio.telegram.handlers.handle_natural_language.

    Args:
        config_dir: Répertoire de config (défaut : répertoire du package devaimazing).

    Returns:
        {"model": str | None, "base_url": str, "num_ctx": int}. "model" est
        None si models.devaimazing n'est pas défini (feature non configurée).

    Raises:
        FileNotFoundError: Si studio.yml est introuvable.
        ValueError: Si studio.yml ou local.yml n'est pas un mapping YAML valide.
    """
    resolved_config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
    global_config = _read_yaml_mapping(
        resolved_config_dir / "studio.yml", required=True, label="Configuration globale"
    )
    local_config = _read_yaml_mapping(
        resolved_config_dir / "local.yml", required=False, label="Configuration locale"
    )
    merged = _deep_merge(global_config, local_config)
    return {
        "model": merged.get("models", {}).get("devaimazing"),
        "base_url": merged.get("ollama", {}).get("base_url", "http://localhost:11434"),
        "num_ctx": merged.get("devaimazing", {}).get("num_ctx", 4096),
    }


def load_global_whisper_config(config_dir: Optional[Path] = None) -> dict:
    """
    Section whisper: de studio.yml, avec override local.yml appliqué (ADR
    0014). Même raison de séparation d'avec StudioConfig que
    load_global_telegram_config/load_global_devaimazing_config : la
    transcription n'est scopée à aucun projet, c'est un prétraitement de la
    couche Telegram avant même la résolution du projet concerné.

    Args:
        config_dir: Répertoire de config (défaut : répertoire du package devaimazing).

    Returns:
        {"base_url": str, "language": str}.

    Raises:
        FileNotFoundError: Si studio.yml est introuvable.
        ValueError: Si studio.yml ou local.yml n'est pas un mapping YAML valide.
    """
    resolved_config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
    global_config = _read_yaml_mapping(
        resolved_config_dir / "studio.yml", required=True, label="Configuration globale"
    )
    local_config = _read_yaml_mapping(
        resolved_config_dir / "local.yml", required=False, label="Configuration locale"
    )
    merged = _deep_merge(global_config, local_config)
    whisper_config = merged.get("whisper", {})
    return {
        "base_url": whisper_config.get("base_url", "http://localhost:8090"),
        "language": whisper_config.get("language", "fr"),
    }


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
        self._config_dir = Path(config_dir) if config_dir is not None else default_config_dir()

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
    def config_dir(self) -> Path:
        """Répertoire de config effectivement utilisé (studio.yml, projects/, local.yml)."""
        return self._config_dir

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
        Crée une config depuis le contexte courant.

        Cherche d'abord un project_name/config_dir posé via project_context()
        (ContextVar, isolé par tâche asyncio — voir son docstring), sinon
        retombe sur les variables d'environnement (chemin CLI historique, un
        seul projet par process) :
            DEVAIMAZING_PROJECT: Nom du projet
            DEVAIMAZING_CONFIG_DIR: Répertoire de config (optionnel)

        Raises:
            ValueError: Si ni le ContextVar ni DEVAIMAZING_PROJECT ne sont définis.
        """
        project_name = _current_project_name.get() or os.environ.get("DEVAIMAZING_PROJECT")
        if not project_name:
            raise ValueError("Variable d'environnement DEVAIMAZING_PROJECT non définie")

        config_dir_raw = _current_config_dir.get() or os.environ.get("DEVAIMAZING_CONFIG_DIR")
        config_dir = Path(config_dir_raw).expanduser() if config_dir_raw else None

        return cls(project_name=project_name, config_dir=config_dir)
