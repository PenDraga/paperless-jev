"""„Verbindung testen“ prüft die Werte im Formular, ohne sie zu speichern (nach #2)."""

from __future__ import annotations

from paperless_jev import app as app_module
from paperless_jev.paperless import Metadata

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
