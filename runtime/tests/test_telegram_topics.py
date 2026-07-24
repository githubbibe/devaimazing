"""
Tests de studio.telegram.topics — vrais fichiers YAML dans tmp_path, pas de
mock (comportement de parsing/résolution exact à vérifier précisément).
"""

from pathlib import Path

from studio.telegram.topics import load_topic_map, resolve_project


def _write_project(projects_dir: Path, name: str, thread_id: int | None) -> None:
    projects_dir.mkdir(parents=True, exist_ok=True)
    content = f"name: {name}\nrepo_path: ~/code/{name}\n"
    if thread_id is not None:
        content += f"telegram:\n  thread_id: {thread_id}\n"
    (projects_dir / f"{name}.yml").write_text(content, encoding="utf-8")


def test_load_topic_map_maps_thread_id_to_project(tmp_path: Path):
    projects_dir = tmp_path / "projects"
    _write_project(projects_dir, "demo", thread_id=111)
    _write_project(projects_dir, "webaimazing", thread_id=222)
    _write_project(projects_dir, "sans-topic", thread_id=None)

    topic_map = load_topic_map(tmp_path)

    assert topic_map == {111: "demo", 222: "webaimazing"}


def test_load_topic_map_missing_projects_dir(tmp_path: Path):
    assert load_topic_map(tmp_path / "inexistant") == {}


def test_resolve_project_general_topic_is_none():
    assert resolve_project(None, {111: "demo"}) is None


def test_resolve_project_known_topic():
    assert resolve_project(111, {111: "demo"}) == "demo"


def test_resolve_project_unknown_topic():
    assert resolve_project(999, {111: "demo"}) is None
