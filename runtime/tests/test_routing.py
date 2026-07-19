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
    phase_agent_sequence,
)
from studio.state import AgentResult, Phase, StudioState


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def config(tmp_path: Path) -> StudioConfig:
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "studio.yml", {"agents": {"max_iterations": 3}})
    _write_yaml(config_dir / "projects" / "demo.yml", {"repo_path": str(tmp_path / "project")})
    return StudioConfig(project_name="demo", config_dir=config_dir)


def test_phase_agent_sequence_filters_stubs_phase():
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_sequence=["back", "back-tu", "front", "front-tu", "test", "secu"],
    )
    assert phase_agent_sequence(state) == ["back", "front"]


def test_phase_agent_sequence_filters_implementation_phase():
    state = StudioState(
        current_phase=Phase.IMPLEMENTATION,
        agent_sequence=["back", "back-tu", "front", "front-tu", "test", "secu"],
    )
    assert phase_agent_sequence(state) == ["back", "back-tu", "front", "front-tu"]


def test_phase_agent_sequence_empty_for_non_multi_agent_phase():
    state = StudioState(current_phase=Phase.SECURITE, agent_sequence=["secu"])
    assert phase_agent_sequence(state) == []


def test_is_last_agent_of_phase():
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=0,
    )
    assert is_last_agent_of_phase(state) is False

    state.current_agent_index = 1
    assert is_last_agent_of_phase(state) is True


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
