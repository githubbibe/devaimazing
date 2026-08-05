"""
Tests des helpers de routage partagés (studio.routing).
"""

from pathlib import Path

import pytest
import yaml

from studio.config import StudioConfig
from studio.routing import (
    agent_iteration_count,
    is_last_agent_of_phase,
    max_iterations_exceeded,
    model_for_attempt,
)
from studio.state import AgentResult, Phase, StudioState


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def config(tmp_path: Path) -> StudioConfig:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "agents": {"max_iterations": 3},
        "models": {"agents_local": "qwen2.5:7b-instruct"},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(tmp_path / "project")})
    return StudioConfig(project_name="demo", config_dir=config_dir)


@pytest.fixture
def config_with_cascade(tmp_path: Path) -> StudioConfig:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {
        "agents": {"max_iterations": 4},
        "models": {"agents_local": ["qwen2.5:7b-instruct", "qwen2.5:14b-instruct", "devstral:24b", "gpt-oss:20b"]},
    })
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(tmp_path / "project")})
    return StudioConfig(project_name="demo", config_dir=config_dir)


def test_is_last_agent_of_phase_non_contiguous_sequence():
    """
    Régression run réel (2026-07-20, voir docs/roadmap.md) : back-tu/
    front-tu s'intercalent entre back/front dans state.agent_sequence
    (séquence complète, jamais une sous-liste filtrée) — is_last_agent_of_
    phase doit avancer sur les 4 rôles de la phase STUBS avant de
    considérer la phase terminée, pas s'arrêter après 2 (l'ancien bug :
    une sous-liste filtrée à 2 éléments comparée à un index progressant
    sur 6 éléments envoyait le node "backend" (rôle back-tu, index 1)
    tourner sous le node "frontend" (identité git front, mauvais prompt).
    """
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_sequence=["back", "back-tu", "front", "front-tu", "test", "secu"],
        current_agent_index=0,
    )
    assert is_last_agent_of_phase(state) is False  # suivant : back-tu, encore STUBS

    state.current_agent_index = 1
    assert is_last_agent_of_phase(state) is False  # suivant : front, encore STUBS

    state.current_agent_index = 2
    assert is_last_agent_of_phase(state) is False  # suivant : front-tu, encore STUBS

    state.current_agent_index = 3
    assert is_last_agent_of_phase(state) is True  # suivant : test, plus dans STUBS


def test_is_last_agent_of_phase_end_of_sequence():
    state = StudioState(
        current_phase=Phase.IMPLEMENTATION,
        agent_sequence=["back", "back-tu"],
        current_agent_index=1,
    )
    assert is_last_agent_of_phase(state) is True  # pas d'élément suivant


def _results(agent: str, phase: Phase, count: int) -> list[AgentResult]:
    return [
        AgentResult(agent=agent, phase=phase, status="feedback_sent", iteration=i + 1)
        for i in range(count)
    ]


def test_agent_iteration_count_counts_matching_agent_and_phase():
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_results=_results("back", Phase.STUBS, 2) + _results("front", Phase.STUBS, 1),
    )
    assert agent_iteration_count(state, "back") == 2
    assert agent_iteration_count(state, "front") == 1
    assert agent_iteration_count(state, "secu") == 0


def test_agent_iteration_count_ignores_other_phase():
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_results=_results("back", Phase.IMPLEMENTATION, 5),
    )
    assert agent_iteration_count(state, "back") == 0


def test_agent_iteration_count_resets_after_success_in_multi_agent_phase():
    """
    Régression run-20260714-205712 (2026-07-19) : back-tu échoue 2 fois
    puis réussit ; l'audit redémarre ensuite la phase STUBS pour corriger
    un AUTRE agent (back). Le compteur de back-tu (phase à agents
    multiples, voir PHASE_AGENT_ROLES) doit repartir de 0 pour sa
    prochaine tentative, pas rester bloqué à 3.
    """
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_results=[
            AgentResult(agent="back-tu", phase=Phase.STUBS, status="feedback_sent", iteration=1),
            AgentResult(agent="back-tu", phase=Phase.STUBS, status="feedback_sent", iteration=2),
            AgentResult(agent="back-tu", phase=Phase.STUBS, status="success", iteration=3),
        ],
    )
    assert agent_iteration_count(state, "back-tu") == 0


def test_agent_iteration_count_still_counts_failures_after_a_success():
    """
    Après le reset dû au succès, de nouveaux échecs recomptent bien à
    partir de 0 — pas de contournement permanent du garde-fou.
    """
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_results=[
            AgentResult(agent="back-tu", phase=Phase.STUBS, status="success", iteration=1),
            AgentResult(agent="back-tu", phase=Phase.STUBS, status="feedback_sent", iteration=1),
            AgentResult(agent="back-tu", phase=Phase.STUBS, status="feedback_sent", iteration=2),
        ],
    )
    assert agent_iteration_count(state, "back-tu") == 2


def test_agent_iteration_count_does_not_reset_on_success_for_single_agent_phase():
    """
    Non-régression : les phases à agent unique (ex. SECURITE, Sécu) n'ont
    pas ce mécanisme de redo ciblé par groupe — un succès n'y remet pas le
    compteur à zéro, il reste cumulatif sur tout le run (ex. Sécu peut
    ré-émettre un rapport "success" à chaque reprise humaine tant que des
    findings bloquants subsistent ; le garde-fou doit continuer à compter
    ces tentatives).
    """
    state = StudioState(
        current_phase=Phase.SECURITE,
        agent_results=[
            AgentResult(agent="secu", phase=Phase.SECURITE, status="success", iteration=i + 1)
            for i in range(3)
        ],
    )
    assert agent_iteration_count(state, "secu") == 3


def test_max_iterations_not_exceeded_below_threshold(config: StudioConfig):
    state = StudioState(current_phase=Phase.STUBS, agent_results=_results("back", Phase.STUBS, 2))
    assert max_iterations_exceeded(state, config, "back") is False


def test_max_iterations_exceeded_at_threshold(config: StudioConfig):
    state = StudioState(current_phase=Phase.STUBS, agent_results=_results("back", Phase.STUBS, 3))
    assert max_iterations_exceeded(state, config, "back") is True


# --- model_for_attempt (cascade de modèles locaux, ADR 0006 révision 2026-08-05) ---

def test_model_for_attempt_single_string_always_returns_it(config: StudioConfig):
    """models.agents_local en simple chaîne (comportement historique,
    projets qui n'ont pas adopté la cascade) — toujours le même modèle,
    quel que soit le nombre de tentatives déjà faites."""
    state = StudioState(current_phase=Phase.STUBS, agent_results=_results("back", Phase.STUBS, 2))
    assert model_for_attempt(config, state, "back") == "qwen2.5:7b-instruct"


def test_model_for_attempt_cascade_advances_with_iteration_count(
    config_with_cascade: StudioConfig,
):
    """Chaque activation complète déjà tentée avance d'un cran dans la
    cascade — 0 tentative -> premier modèle, 1 tentative -> second, etc."""
    for previous_attempts, expected_model in [
        (0, "qwen2.5:7b-instruct"),
        (1, "qwen2.5:14b-instruct"),
        (2, "devstral:24b"),
        (3, "gpt-oss:20b"),
    ]:
        state = StudioState(
            current_phase=Phase.STUBS,
            agent_results=_results("back", Phase.STUBS, previous_attempts),
        )
        assert model_for_attempt(config_with_cascade, state, "back") == expected_model


def test_model_for_attempt_clamps_beyond_cascade_length(config_with_cascade: StudioConfig):
    """Garde-fou : au-delà de la longueur de la cascade (config
    incohérente, max_iterations > nombre de modèles listés), répète le
    dernier modèle plutôt que de lever une IndexError."""
    state = StudioState(
        current_phase=Phase.STUBS, agent_results=_results("back", Phase.STUBS, 10),
    )
    assert model_for_attempt(config_with_cascade, state, "back") == "gpt-oss:20b"


def test_model_for_attempt_independent_per_agent(config_with_cascade: StudioConfig):
    """La position dans la cascade est propre à chaque agent — back en
    échec ne fait pas avancer la cascade de front."""
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_results=_results("back", Phase.STUBS, 3) + _results("front", Phase.STUBS, 0),
    )
    assert model_for_attempt(config_with_cascade, state, "back") == "gpt-oss:20b"
    assert model_for_attempt(config_with_cascade, state, "front") == "qwen2.5:7b-instruct"


def test_model_for_attempt_raises_on_empty_cascade(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {"models": {}})
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(tmp_path / "project")})
    config = StudioConfig(project_name="demo", config_dir=config_dir)
    state = StudioState(current_phase=Phase.STUBS)

    with pytest.raises(ValueError):
        model_for_attempt(config, state, "back")
