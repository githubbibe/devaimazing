"""
Tests du wrapper Ollama devaimazing.

N'appelle jamais un vrai serveur Ollama : le client ollama.AsyncClient est
remplacé par un faux client scripté (succès/erreurs programmés par appel).
"""

from types import SimpleNamespace

import httpx
import pytest
from ollama import RequestError, ResponseError

import studio.tools.ollama as ollama_tool
from studio.tools.ollama import ExternalServiceError, run_ollama


class _FakeResponse:
    def __init__(self, content: str, prompt_eval_count: int, eval_count: int):
        self.message = SimpleNamespace(content=content)
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count


def _make_fake_client_cls(scripted: list):
    """
    Construit une classe FakeClient dont chaque instanciation (= une tentative
    de run_ollama) consomme le prochain élément de `scripted` : soit une
    exception à lever, soit une _FakeResponse à retourner.
    """
    state = {"calls": 0}

    class _FakeClient:
        def __init__(self, host=None, timeout=None):
            self.host = host
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def chat(self, model, messages, stream=False):
            outcome = scripted[state["calls"]]
            state["calls"] += 1
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    _FakeClient.state = state
    return _FakeClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch):
    """Neutralise le backoff pour garder les tests rapides."""
    async def _instant_sleep(_seconds):
        return None

    monkeypatch.setattr(ollama_tool.asyncio, "sleep", _instant_sleep)


async def test_run_ollama_success(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([_FakeResponse("réponse générée", 42, 7)])
    monkeypatch.setattr(ollama_tool, "AsyncClient", fake_cls)

    result = await run_ollama(
        system_prompt="Tu es l'agent Backend.",
        user_prompt="Fais le truc.",
        model="qwen2.5:7b-instruct",
    )

    assert result["content"] == "réponse générée"
    assert result["tokens_prompt"] == 42
    assert result["tokens_completion"] == 7
    assert result["duration_ms"] >= 0
    assert fake_cls.state["calls"] == 1


async def test_run_ollama_retries_on_connection_error_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([
        ConnectionError("Failed to connect to Ollama"),
        _FakeResponse("ça marche au 2e essai", 10, 5),
    ])
    monkeypatch.setattr(ollama_tool, "AsyncClient", fake_cls)

    result = await run_ollama(
        system_prompt="sys", user_prompt="user", model="qwen2.5:7b-instruct",
    )

    assert result["content"] == "ça marche au 2e essai"
    assert fake_cls.state["calls"] == 2


async def test_run_ollama_retries_on_retryable_response_error(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([
        ResponseError('{"error": "internal error"}', 500),
        _FakeResponse("récupéré après 500", 3, 3),
    ])
    monkeypatch.setattr(ollama_tool, "AsyncClient", fake_cls)

    result = await run_ollama(
        system_prompt="sys", user_prompt="user", model="qwen2.5:7b-instruct",
    )

    assert result["content"] == "récupéré après 500"
    assert fake_cls.state["calls"] == 2


async def test_run_ollama_exhausts_retries_raises_external_service_error(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([
        ConnectionError("down"), ConnectionError("down"), ConnectionError("down"),
    ])
    monkeypatch.setattr(ollama_tool, "AsyncClient", fake_cls)

    with pytest.raises(ExternalServiceError):
        await run_ollama(system_prompt="sys", user_prompt="user", model="qwen2.5:7b-instruct")

    assert fake_cls.state["calls"] == ollama_tool.MAX_ATTEMPTS


async def test_run_ollama_non_retryable_response_error_raises_immediately(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([
        ResponseError('{"error": "model not found"}', 404),
        _FakeResponse("ne devrait jamais être atteint", 1, 1),
    ])
    monkeypatch.setattr(ollama_tool, "AsyncClient", fake_cls)

    with pytest.raises(ExternalServiceError):
        await run_ollama(system_prompt="sys", user_prompt="user", model="qwen2.5:inexistant")

    # Pas de retry sur une erreur 404 (modèle inconnu) : un seul appel.
    assert fake_cls.state["calls"] == 1


async def test_run_ollama_timeout_raises_timeouterror(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([httpx.ReadTimeout("timed out")])
    monkeypatch.setattr(ollama_tool, "AsyncClient", fake_cls)

    with pytest.raises(TimeoutError):
        await run_ollama(
            system_prompt="sys", user_prompt="user", model="qwen2.5:7b-instruct",
            timeout_seconds=1,
        )

    # Pas de retry sur un timeout : un seul appel.
    assert fake_cls.state["calls"] == 1


async def test_run_ollama_request_error_is_retried_then_raises(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([
        RequestError("bad request payload"),
        RequestError("bad request payload"),
        RequestError("bad request payload"),
    ])
    monkeypatch.setattr(ollama_tool, "AsyncClient", fake_cls)

    with pytest.raises(ExternalServiceError):
        await run_ollama(system_prompt="sys", user_prompt="user", model="qwen2.5:7b-instruct")

    assert fake_cls.state["calls"] == ollama_tool.MAX_ATTEMPTS
