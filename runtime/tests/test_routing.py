"""
Tests des helpers de routage partagés (studio.routing).
"""

from studio.routing import is_last_agent_of_phase, phase_agent_sequence
from studio.state import Phase, StudioState


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
