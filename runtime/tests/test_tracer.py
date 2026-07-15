"""
Tests de la trace d'exécution structurée (studio.tools.tracer).
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from studio.state import Phase
from studio.tools.tracer import AgentTracer, RunTracer


def _read_events(trace_path: Path) -> list[dict]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]


def test_emit_appends_one_json_line_per_call(tmp_path: Path):
    tracer = RunTracer(tmp_path / "specs" / "run-1" / "trace.jsonl", run_id="run-1")

    tracer.emit("node_enter", agent="back", phase="STUBS")
    tracer.emit("node_exit", agent="back", phase="STUBS", status="success")

    events = _read_events(tracer.trace_path)
    assert len(events) == 2
    assert events[0]["event"] == "node_enter"
    assert events[1]["event"] == "node_exit"
    assert events[1]["status"] == "success"


def test_emit_writes_run_id_and_iso_timestamp(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-042")

    tracer.emit("warning", message="test")

    event = _read_events(tracer.trace_path)[0]
    assert event["run_id"] == "run-042"
    assert "T" in event["ts"]  # ISO 8601


def test_emit_defaults_agent_and_phase_to_none(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1")

    tracer.emit("run_start")

    event = _read_events(tracer.trace_path)[0]
    assert event["agent"] is None
    assert event["phase"] is None


def test_emit_creates_parent_directories(tmp_path: Path):
    trace_path = tmp_path / "specs" / "run-1" / "trace.jsonl"
    tracer = RunTracer(trace_path, run_id="run-1")

    tracer.emit("run_start")

    assert trace_path.is_file()


def test_for_run_derives_path_from_config_specs_dir(tmp_path: Path):
    config = Mock()
    config.repo_path = tmp_path
    config.get = Mock(return_value={"specs_dir": "specs/"})

    tracer = RunTracer.for_run(config, run_id="run-042")

    assert tracer.trace_path == tmp_path / "specs/" / "run-042" / "trace.jsonl"
    config.get.assert_called_with("structure", {})


def test_for_run_defaults_specs_dir_when_structure_key_missing(tmp_path: Path):
    config = Mock()
    config.repo_path = tmp_path
    config.get = Mock(return_value={})

    tracer = RunTracer.for_run(config, run_id="run-042")

    assert tracer.trace_path == tmp_path / "specs/" / "run-042" / "trace.jsonl"


def test_for_agent_binds_agent_and_phase_name(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1")

    bound = tracer.for_agent("back-tu", Phase.STUBS)
    bound.emit("card_written", path="backend/main.py")

    event = _read_events(tracer.trace_path)[0]
    assert event["agent"] == "back-tu"
    assert event["phase"] == "STUBS"
    assert event["path"] == "backend/main.py"


def test_for_agent_accepts_plain_string_phase(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1")

    bound = tracer.for_agent("pm", "CADRAGE")
    bound.emit("warning")

    event = _read_events(tracer.trace_path)[0]
    assert event["phase"] == "CADRAGE"


def test_agent_tracer_isolated_from_other_bindings(tmp_path: Path):
    tracer = RunTracer(tmp_path / "trace.jsonl", run_id="run-1")

    back = tracer.for_agent("back", Phase.STUBS)
    front = tracer.for_agent("front", Phase.STUBS)
    back.emit("node_enter")
    front.emit("node_enter")

    events = _read_events(tracer.trace_path)
    assert [e["agent"] for e in events] == ["back", "front"]


def test_multiple_emits_append_rather_than_overwrite(tmp_path: Path):
    trace_path = tmp_path / "trace.jsonl"
    RunTracer(trace_path, run_id="run-1").emit("run_start")
    RunTracer(trace_path, run_id="run-1").emit("run_end")

    events = _read_events(trace_path)
    assert [e["event"] for e in events] == ["run_start", "run_end"]
