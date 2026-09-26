"""Durchlauf Webhook -> Jev -> Paperless mit Attrappen für beide APIs."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from paperless_jev import app as app_module
from paperless_jev import processor as processor_module
from paperless_jev.paperless import Metadata

from .test_classifier import jev_response, make_doc, make_meta


class FakePaperless:
    docs: dict[int, dict] = {}
    patches: list[tuple[int, dict]] = []
    meta: Metadata = make_meta()

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def document(self, doc_id):
        return copy.deepcopy(self.docs[doc_id])

    async def metadata(self):
        return self.meta

    async def similar_documents(self, doc_id, limit):
        return []

    async def example_titles(self, kind, object_id, limit):
        return ["Beispiel"] if (kind, object_id) == ("document_type", 10) else []

    async def inbox_document_ids(self):
        return list(self.docs)

    async def create_tag(self, name):
        tid = max(self.meta.tags) + 1
        self.meta.tags[tid] = name
        return tid

    async def patch_document(self, doc_id, data):
        self.patches.append((doc_id, data))
        self.docs[doc_id].update(data)
        return self.docs[doc_id]


class FakeJev:
    response: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def ask(self, state, questions, model):
        return copy.deepcopy(self.response)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(processor_module, "PaperlessClient", FakePaperless)
    monkeypatch.setattr(processor_module, "JevClient", FakeJev)
    FakePaperless.docs = {42: make_doc()}
    FakePaperless.patches = []
    FakePaperless.meta = make_meta()
    FakeJev.response = jev_response()
    with TestClient(app_module.app) as c:
        cfg = c.app.state.config
        cfg.update({"typesafe_api_key": "test", "mode": "auto", "poll_minutes": 0})
        cfg.save_instance(None, "home", "http://paperless:8000", "", "tok", True)
        yield c


def _wait(c):
    import time

    proc = c.app.state.processor
    for _ in range(100):
        if proc.queue.qsize() == 0 and not c.app.state.db.one(
            "SELECT id FROM jobs WHERE status IN ('queued','running')"
        ):
            return
        time.sleep(0.05)
    raise AssertionError("Queue läuft nicht leer")


def test_webhook_auto_with_review(client):
    secret = client.app.state.config.all()["webhook_secret"]
    assert client.post("/hook/home?token=wrong", data={"doc_id": "42"}).status_code == 403
    r = client.post(f"/hook/home?token={secret}", data={"doc_id": "42"})
    assert r.json()["queued"] is True
    _wait(client)

    job = client.app.state.db.one("SELECT * FROM jobs")
    assert job["status"] == "review", job["error"]
    doc_id, data = FakePaperless.patches[-1]
    assert data["document_type"] == 10
    assert data["created"] == "2026-03-12"
    review_tag = FakePaperless.meta.tag_id("ai-review")
    assert review_tag in data["tags"] and 100 in data["tags"]  # Posteingang bleibt

    # Review-Seite rendert und Übernahme schließt das Dokument ab
    assert "Swisscom" in client.get("/review").text
    r = client.post(
        f"/jobs/{job['id']}/apply",
        data={"use_correspondent": "on", "correspondent": "2", "use_document_type": "on",
              "document_type": "10", "tags": ["101"], "action": "apply"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    _, data = FakePaperless.patches[-1]
    assert data["correspondent"] == 2
    assert 101 in data["tags"] and 100 not in data["tags"] and review_tag not in data["tags"]
    assert FakePaperless.meta.tag_id("ai-klassifiziert") in data["tags"]
    job = client.app.state.db.job(job["id"])
    assert job["status"] == "done"
    assert job["applied"]["corrections"]["correspondent"] == {"suggested": 1, "chosen": 2}


def test_auto_done_when_confident(client):
    FakeJev.response["answers"]["correspondent"]["confidence"] = 0.95
    FakeJev.response["answers"]["tag:101"]["noul"] = 0.97
    client.app.state.config.update({"title_enabled": True})
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    job = client.app.state.db.one("SELECT * FROM jobs")
    assert job["status"] == "done", job["error"]
    _, data = FakePaperless.patches[-1]
    assert data["correspondent"] == 1
    assert data["title"] == "Rechnung Swisscom 2026-03"
    assert 100 not in data["tags"] and 101 in data["tags"]


def test_dry_run_writes_nothing(client):
    client.app.state.config.update({"mode": "dry_run"})
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    assert client.app.state.db.one("SELECT status FROM jobs")["status"] == "dry_run"
    assert FakePaperless.patches == []
    # zweites Polling reiht dasselbe Dokument nicht erneut ein
    client.post("/run", data={"instance_id": "0"})
    assert client.app.state.db.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 1


@pytest.mark.parametrize("path", ["/", "/setup", "/rules", "/log", "/review", "/descriptions", "/jobs/1"])
def test_pages_render(client, path):
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    assert client.get(path).status_code == 200


def test_ollama_title_and_manual_title(client, monkeypatch):
    calls = []

    async def fake_title(llm, text, facts, examples):
        calls.append((llm.url, llm.model, facts["Absender"], examples))
        return "Swisscom - Rechnung Mobile"

    monkeypatch.setattr(processor_module, "generate_title", fake_title)
    FakeJev.response["answers"]["correspondent"]["confidence"] = 0.95
    FakeJev.response["answers"]["tag:101"]["noul"] = 0.97
    # alte Einstellungsnamen (v0.2) werden übernommen
    client.app.state.config.update({"title_mode": "ollama", "ollama_url": "http://ollama:11434", "ollama_model": "qwen3:8b"})
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    _, data = FakePaperless.patches[-1]
    assert data["title"] == "Swisscom - Rechnung Mobile"
    assert calls[0][:3] == ("http://ollama:11434", "qwen3:8b", "Swisscom")

    # Im Review eingetragener Titel hat Vorrang vor Ollama
    job = client.app.state.db.one("SELECT id FROM jobs")
    client.post(f"/jobs/{job['id']}/apply", data={"title": "Mein Titel", "action": "apply"})
    _, data = FakePaperless.patches[-1]
    assert data["title"] == "Mein Titel"
    assert len(calls) == 1


def test_descriptions_are_expanded_on_change(client, monkeypatch):
    calls = []

    async def fake_describe(llm, kind, name, notes, examples, lang="de"):
        calls.append((kind, name, notes, examples, lang))
        return "Ausformuliert: " + notes

    monkeypatch.setattr(app_module, "describe_category", fake_describe)
    cfg = client.app.state.config
    cfg.update({"llm_provider": "openai", "llm_url": "http://sglang:8000", "llm_model": "qwen3.8-flash-next"})
    form = {"instance_id": "1", "kind": "tag", "ids": ["101", "103"], "name_101": "Steuern", "name_103": "Auto",
            "text_101": "Für die Steuererklärung relevant", "active_101": "on", "text_103": "", "active_103": "on",
            "expand": "on"}
    assert client.post("/descriptions", data=form, follow_redirects=False).status_code == 303
    d = cfg.descriptions(1)
    assert d[("tag", 101)] == {"text": "Ausformuliert: Für die Steuererklärung relevant", "source": "Für die Steuererklärung relevant", "active": True}
    assert calls == [("tag", "Steuern", "Für die Steuererklärung relevant", [], "de")]

    # Unverändert gespeichert -> nicht erneut ausformuliert
    client.post("/descriptions", data=form)
    assert len(calls) == 1
    # Seite zeigt Stichworte und ausformulierte Fassung
    page = client.get("/descriptions?instance_id=1&kind=tag").text
    assert "Für die Steuererklärung relevant</textarea>" in page and "→ Ausformuliert: Für die" in page

    # Englische Oberfläche -> englische Beschreibung
    client.cookies.set("lang", "en")
    client.post("/descriptions", data=form | {"text_101": "tax return"})
    assert calls[-1][-1] == "en"
    client.cookies.set("lang", "de")


def test_language_switch(client):
    assert "Übersicht" in client.get("/").text
    r = client.get("/lang/en?next=/rules", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/rules"
    client.cookies.set("lang", "en")
    page = client.get("/rules").text
    assert "Operating mode" in page and "Betriebsmodus" not in page and '<html lang="en">' in page
    assert "Overview" in client.get("/log").text
    r = client.post("/rules", data={"mode": "dry_run"}, follow_redirects=False)
    assert "Rules+saved" in r.headers["location"]
    client.cookies.set("lang", "de")
    assert client.get("/lang/en?next=//evil.example", follow_redirects=False).headers["location"] == "/"



def test_document_preview_is_proxied(client, monkeypatch):
    async def preview(self, doc_id):
        return b"%PDF-1.7 test", "application/pdf"

    monkeypatch.setattr(FakePaperless, "preview", preview, raising=False)
    r = client.get("/doc/1/42")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"
    assert "inline" in r.headers["content-disposition"]
    assert client.get("/doc/99/42").status_code == 404
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    assert 'href="/doc/1/42"' in client.get("/jobs/1").text



def test_single_document_test_never_writes_and_does_not_block_polling(client):
    # Modus "auto" aus der Fixture - ein Test darf trotzdem nichts schreiben
    FakeJev.response["answers"]["correspondent"]["confidence"] = 0.95
    r = client.post("/test", data={"doc": "https://paperless.example/documents/42/details"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    job = client.app.state.db.one("SELECT * FROM jobs")
    assert (job["source"], job["status"]) == ("test", "dry_run")
    assert job["result"]["fields"]["correspondent"]["value"] == 1
    assert FakePaperless.patches == []
    page = client.get(r.headers["location"]).text
    assert "nicht in Paperless geschrieben" in page

    # Das normale Polling verarbeitet das Dokument danach trotzdem
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    assert client.app.state.db.one("SELECT COUNT(*) AS n FROM jobs WHERE source = 'manual' OR source = 'poll'")["n"] == 1
    assert FakePaperless.patches


def test_parse_doc_id():
    from paperless_jev.app import _parse_doc_id

    assert _parse_doc_id(" 2474 ") == 2474
    assert _parse_doc_id("#12") == 12
    assert _parse_doc_id("https://paperless.example.com/documents/2474/details") == 2474
    assert _parse_doc_id("abc") is None


def test_log_cleanup_and_delete(client):
    db = client.app.state.db
    ids = {}
    for name, status, source in [("err", "error", "poll"), ("test", "dry_run", "test"), ("dry", "dry_run", "poll"),
                                 ("done", "done", "poll"), ("run", "running", "test")]:
        ids[name] = db.create_job(1, 900 + len(ids), source)
        db.update_job(ids[name], status=status)
    page = client.get("/log").text
    assert "Protokoll bereinigen" in page

    # Fehler + Einzeltests: der laufende Test bleibt stehen, Probelauf und Erledigt auch
    r = client.post("/log/cleanup", data={"what": ["error", "test"]}, follow_redirects=False)
    assert r.status_code == 303 and "2+Eintr" in r.headers["location"]
    left = {row["id"] for row in db.query("SELECT id FROM jobs WHERE doc_id >= 900")}
    assert left == {ids["dry"], ids["done"], ids["run"]}

    # Einzelner Eintrag; laufende lassen sich nicht löschen
    client.post(f"/jobs/{ids['dry']}/delete")
    assert db.job(ids["dry"]) is None
    r = client.post(f"/jobs/{ids['run']}/delete", follow_redirects=False)
    assert "err=" in r.headers["location"] and db.job(ids["run"])


def test_suggest_title_writes_nothing(client, monkeypatch):
    async def fake_title(llm, text, facts, examples):
        return "Swisscom - Rechnung Mobile"

    monkeypatch.setattr(processor_module, "generate_title", fake_title)
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    job = client.app.state.db.one("SELECT id FROM jobs")
    patches = len(FakePaperless.patches)

    client.app.state.config.update({"title_mode": "off"})
    assert "ausgeschaltet" in client.post(f"/jobs/{job['id']}/title").text

    client.app.state.config.update({"title_mode": "llm", "llm_provider": "ollama", "llm_url": "http://ollama:11434", "llm_model": "qwen3:8b"})
    r = client.post(f"/jobs/{job['id']}/title")
    assert "Swisscom - Rechnung Mobile" in r.text and 'data-title="Swisscom - Rechnung Mobile"' in r.text
    assert len(FakePaperless.patches) == patches
    assert "Titel vorschlagen" in client.get(f"/jobs/{job['id']}").text


def test_log_filters_and_back_after_apply(client):
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    job = client.app.state.db.one("SELECT id, result FROM jobs")
    dt = job["result"]["fields"]["document_type"]["label"]
    page = client.get("/log").text
    from urllib.parse import quote_plus
    assert "alle Dokumenttypen" in page and f'/log?document_type={quote_plus(dt)}' in page.replace("%20", "+")
    assert f"/jobs/{job['id']}" in client.get("/log", params={"document_type": dt}).text
    assert "Noch keine Einträge" in client.get("/log?document_type=gibtesnicht").text
    assert "Noch keine Einträge" in client.get("/log?tag=gibtesnicht").text

    # Job-Seite merkt sich die Liste, von der man kam; nach dem Übernehmen geht es dorthin zurück
    page = client.get(f"/jobs/{job['id']}", headers={"referer": "http://testserver/log?status=review&msg=x"}).text
    assert 'name="back" value="/log?status=review"' in page
    r = client.post(f"/jobs/{job['id']}/apply", data={"action": "apply", "back": "/log?status=review"}, follow_redirects=False)
    assert r.headers["location"].startswith("/log?status=review&msg=")
    # fremde Ziele werden ignoriert
    r = client.post(f"/jobs/{job['id']}/apply", data={"action": "dismiss", "back": "//evil.example/x"}, follow_redirects=False)
    assert r.headers["location"].startswith("/review?")
    assert 'value="/log"' in client.get(f"/jobs/{job['id']}", headers={"referer": "https://evil.example/log"}).text


def test_log_shows_open_entries_by_default(client):
    db = client.app.state.db
    done = db.create_job(1, 950, "poll"); db.update_job(done, status="done", doc_title="Erledigtes Dok")
    rev = db.create_job(1, 951, "poll"); db.update_job(rev, status="review", doc_title="Offenes Dok")
    page = client.get("/log").text
    assert "Offenes Dok" in page and "Erledigtes Dok" not in page
    assert "Erledigtes Dok" in client.get("/log?status=closed").text
    page = client.get("/log?status=all").text
    assert "Offenes Dok" in page and "Erledigtes Dok" in page
    page = client.get("/").text
    assert "Offenes Dok" in page and "Erledigtes Dok" not in page
