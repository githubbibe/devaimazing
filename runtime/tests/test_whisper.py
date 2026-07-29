"""
Tests du wrapper whisper.cpp devaimazing (ADR 0014).

N'appelle jamais un vrai serveur whisper.cpp : httpx.AsyncClient est
remplacé par un faux client scripté, même principe que test_ollama.py.
"""

import httpx
import pytest
import studio.tools.whisper as whisper_tool
from studio.tools.whisper import ExternalServiceError, transcribe_voice_message


class _FakeResponse:
    def __init__(self, json_data: dict, status_code: int = 200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = str(json_data)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://test/inference")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("erreur", request=request, response=response)

    def json(self):
        return self._json_data


def _make_fake_client_cls(scripted: list):
    state = {"calls": 0}

    class _FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, files=None, data=None):
            state.setdefault("urls", []).append(url)
            state.setdefault("files", []).append(files)
            state.setdefault("data", []).append(data)
            outcome = scripted[state["calls"]]
            state["calls"] += 1
            if isinstance(outcome, BaseException):
                raise outcome
            outcome.raise_for_status()
            return outcome

    _FakeClient.state = state
    return _FakeClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch):
    async def _instant_sleep(_seconds):
        return None

    monkeypatch.setattr(whisper_tool.asyncio, "sleep", _instant_sleep)


async def test_transcribe_success(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([_FakeResponse({"text": "  bonjour le studio  "})])
    monkeypatch.setattr(whisper_tool.httpx, "AsyncClient", fake_cls)

    text = await transcribe_voice_message(b"audio-bytes", base_url="http://test:8090")

    assert text == "bonjour le studio"
    assert fake_cls.state["urls"] == ["http://test:8090/inference"]
    assert fake_cls.state["data"] == [{"language": "fr", "response_format": "json"}]


async def test_transcribe_empty_speech_returns_empty_string(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([_FakeResponse({"text": ""})])
    monkeypatch.setattr(whisper_tool.httpx, "AsyncClient", fake_cls)

    text = await transcribe_voice_message(b"silence", base_url="http://test:8090")

    assert text == ""


async def test_transcribe_passes_custom_language(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([_FakeResponse({"text": "hello"})])
    monkeypatch.setattr(whisper_tool.httpx, "AsyncClient", fake_cls)

    await transcribe_voice_message(b"audio", base_url="http://test:8090", language="en")

    assert fake_cls.state["data"] == [{"language": "en", "response_format": "json"}]


async def test_transcribe_4xx_is_not_retried(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([_FakeResponse({"error": "bad request"}, status_code=400)])
    monkeypatch.setattr(whisper_tool.httpx, "AsyncClient", fake_cls)

    with pytest.raises(ExternalServiceError):
        await transcribe_voice_message(b"audio", base_url="http://test:8090")

    assert fake_cls.state["calls"] == 1


async def test_transcribe_retries_on_connection_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_cls = _make_fake_client_cls([
        httpx.ConnectError("connexion refusée"),
        _FakeResponse({"text": "ca marche"}),
    ])
    monkeypatch.setattr(whisper_tool.httpx, "AsyncClient", fake_cls)

    text = await transcribe_voice_message(b"audio", base_url="http://test:8090")

    assert text == "ca marche"
    assert fake_cls.state["calls"] == 2


async def test_transcribe_exhausts_retries_raises_external_service_error(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_cls = _make_fake_client_cls([
        httpx.ConnectError("connexion refusée"),
        httpx.ConnectError("connexion refusée"),
        httpx.ConnectError("connexion refusée"),
    ])
    monkeypatch.setattr(whisper_tool.httpx, "AsyncClient", fake_cls)

    with pytest.raises(ExternalServiceError):
        await transcribe_voice_message(b"audio", base_url="http://test:8090")

    assert fake_cls.state["calls"] == whisper_tool.MAX_ATTEMPTS


async def test_transcribe_timeout_raises_timeout_error(monkeypatch: pytest.MonkeyPatch):
    fake_cls = _make_fake_client_cls([httpx.TimeoutException("trop long")])
    monkeypatch.setattr(whisper_tool.httpx, "AsyncClient", fake_cls)

    with pytest.raises(TimeoutError):
        await transcribe_voice_message(b"audio", base_url="http://test:8090", timeout_seconds=1)
