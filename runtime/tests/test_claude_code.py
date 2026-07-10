"""
Tests du wrapper Claude Code CLI devaimazing.

N'appelle jamais le vrai binaire `claude` (coûterait des tokens API réels) :
asyncio.create_subprocess_exec est remplacé par un faux sous-process scripté.
"""

import asyncio
import json
from pathlib import Path

import pytest

import studio.tools.claude_code as claude_code_tool
from studio.tools.claude_code import run_claude_code


class _FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int, hang: bool = False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self, input=None):
        if self._hang:
            await asyncio.sleep(999)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _fake_subprocess_exec(fake_process: _FakeProcess):
    async def _create(*args, **kwargs):
        return fake_process

    return _create


def _success_payload(**overrides) -> bytes:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "contenu généré",
        "usage": {"input_tokens": 10, "output_tokens": 71},
        "duration_ms": 1995,
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


async def test_run_claude_code_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake_process = _FakeProcess(stdout=_success_payload(), stderr=b"", returncode=0)
    monkeypatch.setattr(
        claude_code_tool.asyncio, "create_subprocess_exec", _fake_subprocess_exec(fake_process)
    )

    result = await run_claude_code(prompt="fais le truc", model="claude-opus-4-8", cwd=tmp_path)

    assert result["content"] == "contenu généré"
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 71}
    assert result["duration_ms"] == 1995


async def test_run_claude_code_nonzero_exit_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fake_process = _FakeProcess(stdout=b"", stderr=b"erreur fatale", returncode=1)
    monkeypatch.setattr(
        claude_code_tool.asyncio, "create_subprocess_exec", _fake_subprocess_exec(fake_process)
    )

    with pytest.raises(RuntimeError):
        await run_claude_code(prompt="x", model="claude-opus-4-8", cwd=tmp_path)


async def test_run_claude_code_is_error_in_json_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fake_process = _FakeProcess(
        stdout=_success_payload(is_error=True, result="max turns exceeded"),
        stderr=b"", returncode=0,
    )
    monkeypatch.setattr(
        claude_code_tool.asyncio, "create_subprocess_exec", _fake_subprocess_exec(fake_process)
    )

    with pytest.raises(RuntimeError):
        await run_claude_code(prompt="x", model="claude-opus-4-8", cwd=tmp_path)


async def test_run_claude_code_invalid_json_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fake_process = _FakeProcess(stdout=b"pas du json", stderr=b"", returncode=0)
    monkeypatch.setattr(
        claude_code_tool.asyncio, "create_subprocess_exec", _fake_subprocess_exec(fake_process)
    )

    with pytest.raises(ValueError):
        await run_claude_code(prompt="x", model="claude-opus-4-8", cwd=tmp_path)


async def test_run_claude_code_timeout_kills_process_and_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fake_process = _FakeProcess(stdout=b"", stderr=b"", returncode=0, hang=True)
    monkeypatch.setattr(
        claude_code_tool.asyncio, "create_subprocess_exec", _fake_subprocess_exec(fake_process)
    )

    with pytest.raises(TimeoutError):
        await run_claude_code(
            prompt="x", model="claude-opus-4-8", cwd=tmp_path, timeout_seconds=0.05,
        )

    assert fake_process.killed is True
