"""Carrier history tests use synthetic data and no real provider credentials."""
import pytest
from unittest.mock import patch
import httpx

from controlcentre import agentphone_history as history


def test_history_preserves_exact_caller_id_time_and_carrier_duration(monkeypatch):
    requests = []
    def fake_get(path, params=None):
        requests.append((path, params))
        if params["direction"] == "inbound":
            return {"data": [
                {"id": "call_inbound_123", "direction": "inbound", "fromNumber": "+17055550123",
                 "startedAt": "2026-10-26T21:32:27.415Z", "durationSeconds": 14,
                 "status": "completed", "lastTranscriptSnippet": "Hello"},
                {"id": "short", "direction": "inbound", "fromNumber": "+17055550124",
                 "startedAt": "2026-10-26T21:31:00Z", "durationSeconds": 15},
            ]}
        return {"data": [{"id": "call_outbound_456", "direction": "outbound",
                         "toNumber": "+17055550199", "startedAt": "2026-10-25T11:00:00Z",
                         "durationSeconds": None, "status": "completed"}]}
    monkeypatch.setattr(history, "_get", fake_get)
    calls = history.list_calls()
    assert len(calls) == 2
    assert calls[0]["number"] == "+17055550123"
    assert calls[0]["duration_seconds"] == 14
    assert calls[0]["started_at"] == "2026-10-26T21:32:27.415Z"
    assert calls[1]["direction"] == "outbound"
    assert calls[1]["duration_seconds"] is None
    assert requests == [("/calls", {"limit": 30, "direction": "inbound"}),
                        ("/calls", {"limit": 30, "direction": "outbound"})]


def test_transcripts_are_explicitly_unavailable_when_missing(monkeypatch):
    monkeypatch.setattr(history, "_get", lambda *a, **k: {"transcript": []})
    assert history.call_transcript("call_123456789") == []


def test_transcript_role_normalization(monkeypatch):
    monkeypatch.setattr(history, "_get", lambda *a, **k: {"transcript": [
        {"role": "user", "content": "Hello"},
        {"role": "agent", "content": "Good afternoon"},
        {"role": "unknown", "content": "Never show"}]})
    assert history.call_transcript("call_123456789") == [
        {"speaker": "Caller", "text": "Hello"}, {"speaker": "Agent", "text": "Good afternoon"}]


def test_untrusted_call_ids_rejected_before_network(monkeypatch):
    monkeypatch.setattr(history, "_get", lambda *a, **k: pytest.fail("network used"))
    with pytest.raises(history.HistoryUnavailable):
        history.call_transcript("../secrets")


def test_redirection_does_not_forward_provider_secret(monkeypatch):
    seen = []
    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(302, headers={"location": "https://evil.invalid/steal"})
    monkeypatch.setattr(history, "_token", lambda: "synthetic-provider-key")
    original_client = httpx.Client
    def mock_client(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(history.httpx, "Client", mock_client)
    with pytest.raises(history.HistoryUnavailable):
        history._get("/calls", {"limit": 2})
    assert seen == ["api.agentphone.ai"]


def test_history_routes_are_authenticated_and_demo_fail_closed(tmp_path):
    from fastapi.testclient import TestClient
    from controlcentre.app import create_app
    app = create_app(tmp_path, tmp_path, demo=True)
    client = TestClient(app, base_url="http://127.0.0.1:8787", client=("127.0.0.1", 1234))
    assert client.get("/api/agentphone/calls").status_code == 401
    assert client.get("/api/agentphone/calls/call_123456789/transcript").status_code == 401
    assert client.post("/api/auth/setup", json={"password": "synthetic-history-password"}).status_code == 200
    assert client.get("/api/agentphone/calls").status_code == 409
    assert client.get("/api/agentphone/calls/call_123456789/transcript").status_code == 409



def test_authenticated_carrier_routes_return_provider_data_without_writing_local_history(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from controlcentre.app import create_app
    app = create_app(tmp_path, tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1:8787", client=("127.0.0.1", 1234))
    monkeypatch.setattr(history, "list_calls", lambda: [
        {"id": "call_sample_123", "direction": "inbound", "number": "+17055550123",
         "started_at": "2026-10-26T21:32:27Z", "duration_seconds": 14, "status": "completed"}])
    monkeypatch.setattr(history, "call_transcript", lambda _: [
        {"speaker": "Caller", "text": "Synthetic hello"}])
    setup = client.post("/api/auth/setup", json={"password": "synthetic-history-password"})
    assert setup.status_code == 200
    result = client.get("/api/agentphone/calls")
    assert result.status_code == 200
    assert result.json()["calls"][0]["number"] == "+17055550123"
    transcript = client.get("/api/agentphone/calls/call_sample_123/transcript")
    assert transcript.status_code == 200
    assert transcript.json()["transcript"][0]["text"] == "Synthetic hello"
    assert app.state.store.calls() == []
