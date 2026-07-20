"""
Tests du graphe LangGraph devaimazing (router, should_checkpoint, build_graph).

Les nodes eux-mêmes (studio.nodes.*) sont encore des stubs (corps `...`) —
ces tests ne les invoquent pas via le graphe compilé, ils vérifient
uniquement le câblage (router, checkpoints, construction du graphe).
"""

from pathlib import Path

import pytest
import yaml
from langgraph.graph import END

from studio.config import StudioConfig
from studio.graph import build_graph, should_checkpoint, router
from studio.state import AgentResult, Phase, RunStatus, StudioState


# --- router() -----------------------------------------------------------

def test_router_reception_and_cadrage_route_to_pm():
    assert router(StudioState(current_phase=Phase.RECEPTION)) == "pm"
    assert router(StudioState(current_phase=Phase.CADRAGE)) == "pm"


def test_router_audit_amont_routes_to_architect():
    assert router(StudioState(current_phase=Phase.AUDIT_AMONT)) == "architect"


def test_router_fiches_routes_to_pm():
    assert router(StudioState(current_phase=Phase.FICHES)) == "pm"


def test_router_stubs_phase_indexes_full_sequence_not_filtered():
    """
    Régression run réel (2026-07-20, voir docs/roadmap.md) : ce test
    attendait auparavant router(index=1) == "frontend", ce qui encodait le
    bug lui-même — index 1 dans state.agent_sequence (la séquence
    complète) est "back-tu", pas "front". Le bug envoyait le node
    "backend" (rôle réel back-tu) tourner sous l'identité/prompt "front".
    back-tu/front-tu participent bien à la phase STUBS (voir routing.py::
    PHASE_AGENT_ROLES), donc les 4 premiers index routent tous vers le bon
    node.
    """
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_sequence=["back", "back-tu", "front", "front-tu", "test", "secu"],
        current_agent_index=0,
    )
    assert router(state) == "backend"  # back

    state.current_agent_index = 1
    assert router(state) == "backend"  # back-tu -> même node que back

    state.current_agent_index = 2
    assert router(state) == "frontend"  # front

    state.current_agent_index = 3
    assert router(state) == "frontend"  # front-tu -> même node que front


def test_router_implementation_phase_includes_tu_roles():
    state = StudioState(
        current_phase=Phase.IMPLEMENTATION,
        agent_sequence=["back", "back-tu", "front", "front-tu", "test", "secu"],
        current_agent_index=1,
    )
    assert router(state) == "backend"  # back-tu -> même node que back

    state.current_agent_index = 3
    assert router(state) == "frontend"  # front-tu -> même node que front


def test_router_tests_phase_routes_to_test_node():
    assert router(StudioState(current_phase=Phase.TESTS)) == "test"


def test_router_securite_routes_to_security_node():
    assert router(StudioState(current_phase=Phase.SECURITE)) == "security"


def test_router_audit_aval_routes_to_architect():
    assert router(StudioState(current_phase=Phase.AUDIT_AVAL)) == "architect"


def test_router_cloture_routes_to_closer():
    assert router(StudioState(current_phase=Phase.CLOTURE)) == "closer"


@pytest.mark.parametrize("status", [RunStatus.WAITING_HUMAN, RunStatus.FAILED, RunStatus.COMPLETED])
def test_router_terminal_statuses_return_end(status):
    state = StudioState(current_phase=Phase.IMPLEMENTATION, status=status)
    assert router(state) == END


def test_router_index_out_of_bounds_raises_value_error():
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_sequence=["back", "front"],
        current_agent_index=5,
    )
    with pytest.raises(ValueError):
        router(state)


def test_router_sequence_without_any_role_for_phase_raises_value_error():
    # "agent-inconnu" n'appartient à aucun rôle de PHASE_AGENT_ROLES[STUBS].
    state = StudioState(
        current_phase=Phase.STUBS,
        agent_sequence=["agent-inconnu"],
        current_agent_index=0,
    )
    with pytest.raises(ValueError):
        router(state)


# --- should_checkpoint() -------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    studio_yml = {
        "checkpoints": {
            "phase_1_cadrage": True,
            "phase_2_audit_amont": False,
        },
        "state": {"db_path": str(tmp_path / "state.db")},
    }
    _write_yaml(tmp_path / "studio.yml", studio_yml)
    _write_yaml(tmp_path / "projects" / "demo.yml", {"repo_path": "~/code/demo"})
    return tmp_path


@pytest.fixture(autouse=True)
def _env(config_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEVAIMAZING_PROJECT", "demo")
    monkeypatch.setenv("DEVAIMAZING_CONFIG_DIR", str(config_dir))


def test_should_checkpoint_true_when_awaiting_human_validation_regardless_of_config():
    # phase_2_audit_amont est désactivé dans la config, mais un trou
    # d'intention déjà signalé (awaiting_human_validation) prime toujours
    # (garde-fou ADR 0008 : jamais désactivable).
    state = StudioState(current_phase=Phase.AUDIT_AMONT, awaiting_human_validation=True)
    assert should_checkpoint(state) is True


def test_should_checkpoint_reads_config_enabled():
    state = StudioState(current_phase=Phase.CADRAGE)
    assert should_checkpoint(state) is True


def test_should_checkpoint_reads_config_disabled():
    state = StudioState(current_phase=Phase.AUDIT_AMONT)
    assert should_checkpoint(state) is False


def test_should_checkpoint_false_for_phase_without_checkpoint_key():
    state = StudioState(current_phase=Phase.STUBS)
    assert should_checkpoint(state) is False


# --- build_graph() --------------------------------------------------------

async def test_build_graph_registers_all_nodes_and_uses_sqlite_checkpointer(config_dir: Path):
    config = StudioConfig(project_name="demo", config_dir=config_dir)

    compiled = await build_graph(config)
    try:
        registered = set(compiled.nodes.keys())
        for name in ["pm", "architect", "backend", "frontend", "test", "security", "closer"]:
            assert name in registered
        assert (config_dir / "state.db").is_file()
    finally:
        # build_graph laisse la connexion ouverte par conception (voir sa
        # docstring) ; on la ferme ici pour ne pas polluer la sortie de test.
        await compiled.checkpointer.conn.close()


async def test_build_graph_checkpointer_serde_allows_studio_types_without_warning(
    config_dir: Path, caplog: pytest.LogCaptureFixture
):
    """Régression : LangGraph journalise un warning (et bloquera un jour) la
    désérialisation d'un type non enregistré dans allowed_msgpack_modules —
    constaté en pratique sur Phase/RunStatus/AgentResult (voir
    docs/roadmap.md, backlog 2026-07-14)."""
    config = StudioConfig(project_name="demo", config_dir=config_dir)

    compiled = await build_graph(config)
    try:
        serde = compiled.checkpointer.serde
        values = [
            Phase.FICHES,
            RunStatus.WAITING_HUMAN,
            AgentResult(agent="pm", phase=Phase.FICHES, status="success"),
        ]
        with caplog.at_level("WARNING"):
            for value in values:
                encoded = serde.dumps_typed(value)
                decoded = serde.loads_typed(encoded)
                assert decoded == value

        assert "unregistered type" not in caplog.text
    finally:
        await compiled.checkpointer.conn.close()
