"""„Verbindung testen“ prüft die Werte im Formular, ohne sie zu speichern (nach #2)."""

from __future__ import annotations

import httpx
import pytest

from paperless_jev import app as app_module
from paperless_jev.paperless import Metadata

from .test_llm import _patch
from .test_processor import client  # noqa: F401


class Recorder:
    def __init__(self):
        self.calls = []


def test_classifier_test_uses_unsaved_form_values(client, monkeypatch):  # noqa: F811
    seen = Recorder()

    class Jev:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def ask(self, text, questions, model):
            return {"model": model, "answers": {"t": {"type": "choice", "choice": "invoice", "confidence": 0.9}}}

    def fake_classifier(cfg, timeout=None):
        seen.calls.append(cfg)
        return Jev(), cfg["classifier_model"]

    monkeypatch.setattr(app_module, "classifier", fake_classifier)
    form = {"classifier_kind": "ollama", "classifier_url": "http://lux:8020/", "classifier_model": "lux", "classifier_api_key": "neu"}
    r = client.post("/setup/typesafe/test", data=form)
    assert "invoice" in r.text and 'class="good"' in r.text
    cfg = seen.calls[0]
    assert (cfg["classifier"], cfg["classifier_url"], cfg["classifier_api_key"]) == ("ollama", "http://lux:8020", "neu")
    saved = client.app.state.config.all()
    assert saved["classifier"] == "typesafe" and saved["classifier_api_key"] == ""  # nichts gespeichert


def test_llm_test_uses_unsaved_form_values(client, monkeypatch):  # noqa: F811
    seen = Recorder()

    async def fake_models(llm):
        seen.calls.append(llm)
        return ["qwen3:8b"]

    monkeypatch.setattr(app_module, "list_models", fake_models)
    r = client.post("/rules/llm/test", data={"llm_provider": "ollama", "llm_url": "http://ollama:11434", "llm_model": "qwen3:8b"})
    assert 'class="good"' in r.text
    assert (seen.calls[0].url, seen.calls[0].model) == ("http://ollama:11434", "qwen3:8b")
    assert client.app.state.config.all()["llm_url"] == ""
    assert "URL und Modell eintragen" in client.post("/rules/llm/test", data={"llm_url": ""}).text


def test_instance_test_uses_form_and_keeps_saved_token(client, monkeypatch):  # noqa: F811
    seen = Recorder()

    class Paperless:
        def __init__(self, url, token, timeout=None, host=None):
            seen.calls.append((url, token, host))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def ping(self):
            return 3

        async def metadata(self):
            return Metadata()

    monkeypatch.setattr(app_module, "PaperlessClient", Paperless)
    inst = client.app.state.config.instances()[0]
    # bestehende Instanz: geänderte URL, leeres Token-Feld behält den gespeicherten Token
    r = client.post(f"/setup/instances/{inst.id}/test", data={"url": "http://paperless-neu:8000", "token": "", "public_url": ""})
    assert 'class="good"' in r.text
    assert seen.calls[-1] == ("http://paperless-neu:8000", "tok", None)
    assert client.app.state.config.instance(inst.id).url == "http://paperless:8000"
    # neue Instanz, noch nicht gespeichert
    client.post("/setup/instances/0/test", data={"url": "http://other:8000", "token": "t2", "public_url": "https://docs.example"})
    assert seen.calls[-1] == ("http://other:8000", "t2", "docs.example")
    assert "API-Token eintragen" in client.post("/setup/instances/0/test", data={"url": "http://other:8000"}).text
    assert len(client.app.state.config.instances()) == 1


@pytest.mark.parametrize("failure, detail", [
    ("http", "HTTP 401"),
    ("empty", "Sprachmodell lieferte keine Beschreibung"),
    ("timeout", "Zeitüberschreitung beim Sprachmodell"),
])
def test_description_failure_is_safe_and_retryable(client, monkeypatch, caplog, failure, detail):  # noqa: F811
    cfg = client.app.state.config
    cfg.update({"llm_provider": "openai", "llm_url": "https://model.example/v1",
                "llm_model": "description-model", "llm_api_key": "private-api-key"})
    recovered = False

    def handler(request):
        assert str(request.url) == "https://model.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private-api-key"
        if recovered:
            return httpx.Response(200, json={"choices": [{"message": {"content": "Expanded certificate description"}}]})
        if failure == "timeout":
            raise httpx.ReadTimeout("private-api-key private-provider-detail", request=request)
        if failure == "http":
            return httpx.Response(401, text="private-api-key private-provider-detail")
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    _patch(monkeypatch, handler)
    form = {"instance_id": "1", "kind": "document_type", "ids": "10",
            "name_10": "Certificate", "text_10": "qualification, education",
            "active_10": "on", "expand": "on"}
    response = client.post("/descriptions", data=form)
    assert cfg.descriptions(1)[("document_type", 10)]["text"] == "qualification, education"
    assert detail in response.text
    assert detail in caplog.text
    assert "private-api-key" not in response.text + caplog.text
    assert "private-provider-detail" not in response.text + caplog.text

    recovered = True
    response = client.post("/descriptions", data=form)
    assert cfg.descriptions(1)[("document_type", 10)]["text"] == "Expanded certificate description"
    assert "Expanded certificate description" in response.text


def test_description_can_expand_previously_saved_plain_input(client, monkeypatch):  # noqa: F811
    cfg = client.app.state.config
    cfg.update({"llm_url": "http://ollama:11434", "llm_model": "m"})
    _patch(monkeypatch, lambda request: httpx.Response(200, json={"response": "Expanded notes"}))
    form = {"instance_id": "1", "kind": "tag", "ids": "101", "text_101": "notes", "active_101": "on"}
    client.post("/descriptions", data=form)
    assert cfg.descriptions(1)[("tag", 101)]["text"] == "notes"
    client.post("/descriptions", data=form | {"expand": "on"})
    assert cfg.descriptions(1)[("tag", 101)]["text"] == "Expanded notes"
