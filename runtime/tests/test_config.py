"""
Tests de la configuration devaimazing.
"""

from pathlib import Path

import pytest
import yaml

from studio.config import StudioConfig


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _write_project(config_dir: Path, name: str, data: dict) -> None:
    _write_yaml(config_dir / "projects" / f"{name}.yml", data)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Arborescence config/ minimale et isolée (n'utilise pas config/studio.yml réel)."""
    studio_yml = {
        "models": {"pm_opus": "claude-opus-4-8", "agents_local": "qwen2.5:7b-instruct"},
        "checkpoints": {"phase_1_cadrage": True},
        "ollama": {"base_url": "http://localhost:11434"},
        "metrics": {"db_path": "~/.devaimazing/metrics.db"},
        "state": {"db_path": "~/.devaimazing/state.db"},
        "git": {"base_branch": "develop", "commit_per_task": True},
    }
    _write_yaml(tmp_path / "studio.yml", studio_yml)
    return tmp_path


def test_config_loads_studio_yml(config_dir: Path):
    """Vérifie que studio.yml est chargé sans erreur."""
    _write_project(config_dir, "demo", {"repo_path": "~/code/demo"})

    config = StudioConfig(project_name="demo", config_dir=config_dir)

    assert config.models["pm_opus"] == "claude-opus-4-8"
    assert config.ollama_base_url == "http://localhost:11434"


def test_config_project_overrides_studio(config_dir: Path):
    """Vérifie qu'un paramètre projet écrase le paramètre global."""
    _write_project(
        config_dir,
        "demo",
        {
            "repo_path": "~/code/demo",
            "git": {"base_branch": "main"},
        },
    )

    config = StudioConfig(project_name="demo", config_dir=config_dir)

    assert config.get("git")["base_branch"] == "main"
    # Une clé non redéfinie par le projet doit rester celle de studio.yml
    # (fusion récursive, pas un remplacement complet de la section "git").
    assert config.get("git")["commit_per_task"] is True


def test_config_repo_path_expanded(config_dir: Path):
    """Vérifie que ~ est expandé dans repo_path."""
    _write_project(config_dir, "demo", {"repo_path": "~/code/demo"})

    config = StudioConfig(project_name="demo", config_dir=config_dir)

    assert config.repo_path == Path("~/code/demo").expanduser()
    assert "~" not in str(config.repo_path)


def test_config_missing_project_raises(config_dir: Path):
    """Vérifie qu'un projet inconnu lève FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        StudioConfig(project_name="inconnu", config_dir=config_dir)


def test_config_test_command_none_when_not_defined(config_dir: Path):
    """Aucune commande de test globale par défaut : None si le projet ne la définit pas."""
    _write_project(config_dir, "demo", {"repo_path": "~/code/demo"})

    config = StudioConfig(project_name="demo", config_dir=config_dir)

    assert config.test_command is None


def test_config_test_command_from_project(config_dir: Path):
    """La commande de test est définie par projet (config/projects/<nom>.yml)."""
    _write_project(config_dir, "demo", {
        "repo_path": "~/code/demo",
        "test": {"command": "pytest {target_dir} -q"},
    })

    config = StudioConfig(project_name="demo", config_dir=config_dir)

    assert config.test_command == "pytest {target_dir} -q"


def test_config_local_yml_overrides_studio_and_project(config_dir: Path):
    """local.yml (optionnel, gitignoré) écrase studio.yml et le projet."""
    _write_project(config_dir, "demo", {
        "repo_path": "~/code/demo",
        "notifications": {"ntfy": {"topic": "<PLACEHOLDER_TOPIC>"}},
    })
    _write_yaml(config_dir / "local.yml", {
        "notifications": {"ntfy": {"topic": "un-vrai-secret"}},
    })

    config = StudioConfig(project_name="demo", config_dir=config_dir)

    assert config.get("notifications")["ntfy"]["topic"] == "un-vrai-secret"


def test_config_missing_local_yml_is_not_an_error(config_dir: Path):
    """local.yml est optionnel : son absence ne casse rien."""
    _write_project(config_dir, "demo", {"repo_path": "~/code/demo"})

    config = StudioConfig(project_name="demo", config_dir=config_dir)

    assert config.repo_path == Path("~/code/demo").expanduser()


def test_config_invalid_local_yml_raises_value_error(config_dir: Path):
    """local.yml doit être un mapping, comme studio.yml et le projet."""
    _write_project(config_dir, "demo", {"repo_path": "~/code/demo"})
    (config_dir / "local.yml").write_text("- juste une liste\n- pas un mapping\n", encoding="utf-8")

    with pytest.raises(ValueError):
        StudioConfig(project_name="demo", config_dir=config_dir)


def test_config_from_env_requires_project(monkeypatch: pytest.MonkeyPatch):
    """Vérifie que from_env lève ValueError si DEVAIMAZING_PROJECT est absent."""
    monkeypatch.delenv("DEVAIMAZING_PROJECT", raising=False)

    with pytest.raises(ValueError):
        StudioConfig.from_env()


def test_config_from_env_reads_environment(config_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Vérifie que from_env construit la config à partir des variables d'environnement."""
    _write_project(config_dir, "demo", {"repo_path": "~/code/demo"})
    monkeypatch.setenv("DEVAIMAZING_PROJECT", "demo")
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))

    config = StudioConfig.from_env()

    assert config.project_name == "demo"
