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

    uploads: list[dict] = []
    tasks: dict[str, dict] = {}

    async def post_document(self, content, filename, content_type, title=None, tags=None):
        self.uploads.append({"size": len(content), "filename": filename, "type": content_type, "title": title, "tags": tags})
        return "task-1"

    async def task(self, task_id):
        return self.tasks.get(task_id)

    deleted: list[int] = []

    async def delete_document(self, doc_id):
        self.deleted.append(doc_id)

    objects: list[tuple] = []

    async def create_object(self, kind, name, color=None, parent=None):
        oid = 500 + len(self.objects)
        self.objects.append(("create", kind, name, color))
        self.meta.names(kind)[oid] = name
        if parent:
            self.meta.tag_parents[oid] = parent
        return oid

    async def delete_object(self, kind, object_id):
        self.objects.append(("delete", kind, object_id))
        self.meta.names(kind).pop(object_id, None)

    async def update_object(self, kind, object_id, data):
        self.objects.append(("update", kind, object_id, data))
        if kind == "tag" and "parent" in data:
            if data["parent"]:
                self.meta.tag_parents[object_id] = data["parent"]
            else:
                self.meta.tag_parents.pop(object_id, None)
        if "name" in data:
            self.meta.names(kind)[object_id] = data["name"]

    async def document_ids_with_tag(self, tag_id):
        return [d["id"] for d in self.docs.values() if tag_id in d["tags"]]

    async def documents_with(self, kind, object_id, limit=5):
        docs = [d for d in self.docs.values() if (object_id in d["tags"] if kind == "tag" else d.get(kind) == object_id)]
        return len(docs), [{"id": d["id"], "title": d["title"]} for d in docs[:limit]]

    async def reassign(self, kind, object_id, target):
        self.objects.append(("reassign", kind, object_id, target))
        return 1

    async def add_tag_to_tagged(self, tag_id, tagged_with):
        self.objects.append(("add_tag", tag_id, sorted(tagged_with)))
        return 1


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
    monkeypatch.setattr(processor_module, "classifier", lambda cfg, timeout=None: (FakeJev(), cfg["model"]))
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


def test_existing_tags_shown_as_chips(client):
    db = client.app.state.db
    job = db.create_job(1, 960, "poll")
    db.update_job(job, status="dry_run", doc_title="Mit Tags",
                  result={"fields": {}, "tags": [], "current": {"tags": [100, 101, 102]}})
    page = client.get("/log").text
    # Steuern (101) ist schon gesetzt; Posteingang (100) und der Status-Tag ai-review (102) bleiben verborgen
    assert 'class="conf existing"' in page and "<b>Steuern</b>" in page
    assert "<b>Posteingang</b>" not in page and "<b>ai-review</b>" not in page


def test_review_can_remove_existing_tag(client):
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    job = client.app.state.db.one("SELECT id FROM jobs")
    client.post(f"/jobs/{job['id']}/apply", data={"action": "apply", "shown_tags": ["103"], "tags": ["101"]})
    _, data = FakePaperless.patches[-1]
    assert 103 not in data["tags"] and 101 in data["tags"]


def test_recheck_reviews_replaces_old_entry(client):
    secret = client.app.state.config.all()["webhook_secret"]
    client.post(f"/hook/home?token={secret}", data={"doc_id": "42"})
    _wait(client)
    db = client.app.state.db
    old = db.one("SELECT id, status FROM jobs")
    assert old["status"] == "review"

    r = client.post("/review/recheck", follow_redirects=False)
    assert r.status_code == 303 and "msg=1+" in r.headers["location"]
    _wait(client)
    rows = db.query("SELECT id, status FROM jobs ORDER BY id")
    assert [row["status"] for row in rows] == ["superseded", "review"]
    # Review-Liste zeigt das Dokument nur einmal, mit dem neuen Lauf
    page = client.get("/review").text
    assert page.count('/apply"') == 1 and f'action="/jobs/{rows[1]["id"]}/apply"' in page
    assert "Alle erneut prüfen" in page
    assert "ersetzt" in client.get("/log?status=closed").text


def test_conflicting_existing_tag_is_unticked_in_review(client):
    db = client.app.state.db
    job = db.create_job(1, 42, "poll")
    db.update_job(job, status="review", doc_title="Test", result={
        "fields": {}, "tags": [], "current": {"tags": [101, 103]},
        "tag_checks": [{"id": 101, "label": "Steuern", "p": 0.95, "verdict": "ok"},
                       {"id": 103, "label": "Auto", "p": 0.02, "verdict": "conflict"}]})
    page = client.get(f"/jobs/{job}").text
    assert 'name="keep_tags" value="101" checked' in page
    assert 'name="keep_tags" value="103">' in page


def test_review_shows_one_document_at_a_time(client):
    db = client.app.state.db
    ids = []
    for doc in (42, 43, 44):
        j = db.create_job(1, doc, "poll")
        db.update_job(j, status="review", doc_title=f"Dok {doc}", result={
            "fields": {"correspondent": {"value": 1, "label": "Swisscom", "confidence": 0.8, "level": "suggest",
                                         "top": [["Swisscom", 0.8], ["Stadtwerke", 0.15]]}},
            "tags": [], "current": {"correspondent": 2, "tags": []}})
        ids.append(j)
    page = client.get("/review").text
    assert "1 von 3" in page and "Dok 44" in page and "Dok 42" not in page  # neuestes zuerst
    assert f'name="back" value="/review?after={ids[2]}"' in page
    assert "bisher: Stadtwerke" in page and 'id="rv-data"' in page and "review.js" in page
    assert "2 von 3" in client.get(f"/review?job={ids[1]}").text
    # nach dem Übernehmen von Dok 44 kommt Dok 43
    db.update_job(ids[2], status="done")
    page = client.get(f"/review?after={ids[2]}").text
    assert "Dok 43" in page and "1 von 2" in page
    # Titelvorschlag als JSON
    client.app.state.config.update({"title_mode": "off"})
    assert "error" in client.post(f"/jobs/{ids[0]}/title?format=json").json()


def test_scan_upload_and_status(client):
    FakePaperless.uploads.clear()
    page = client.get("/scan").text
    assert 'id="sc-data"' in page and "scan.js" in page and "Kamera starten" in page

    files = {"file": ("scan.pdf", b"%PDF-1.4 test", "application/pdf")}
    r = client.post("/scan/upload", files=files, data={"instance_id": "1", "title": "Beleg", "tags": ["101", "103"]})
    assert r.status_code == 200 and r.json()["task_id"] == "task-1"
    assert FakePaperless.uploads[-1] == {"size": 13, "filename": "scan.pdf", "type": "application/pdf", "title": "Beleg", "tags": [101, 103]}
    # falscher Typ wird abgelehnt
    bad = client.post("/scan/upload", files={"file": ("x.exe", b"MZ", "application/x-msdownload")}, data={"instance_id": "1"})
    assert bad.status_code == 400

    # Status: Paperless verarbeitet noch -> dann fertig -> paperless-jev klassifiziert selbst
    FakePaperless.tasks = {}
    assert client.get("/scan/status/1/task-1").json()["state"] == "queued"
    FakePaperless.tasks = {"task-1": {"status": "STARTED", "document_id": None, "error": ""}}
    assert client.get("/scan/status/1/task-1").json()["state"] == "processing"
    FakePaperless.tasks = {"task-1": {"status": "SUCCESS", "document_id": 42, "error": ""}}
    assert client.get("/scan/status/1/task-1").json()["state"] == "classifying"
    _wait(client)
    s = client.get("/scan/status/1/task-1").json()
    assert s["state"] == "done" and s["doc_id"] == 42 and s["fields"] and s["browse_url"].endswith("/documents/42/details")
    FakePaperless.tasks = {"task-1": {"status": "FAILURE", "document_id": None, "error": "kaputt"}}
    assert client.get("/scan/status/1/task-1").json() == {"state": "error", "error": "kaputt"}


async def test_task_status_paperless_2_and_3():
    from paperless_jev.paperless import PaperlessClient

    class Fake(PaperlessClient):
        def __init__(self, data):
            self.data = data

        async def _get(self, path, params=None):
            return self.data

    v3 = {"count": 1, "results": [{"status": "success", "result_data": {"document_id": 2496}, "related_document_ids": [2496]}]}
    v2 = [{"status": "SUCCESS", "related_document": "77", "result": "Success. New document id 77 created"}]
    fail = {"results": [{"status": "failure", "result_data": {"error": "Duplikat"}, "related_document_ids": []}]}
    assert await Fake(v3).task("x") == {"status": "SUCCESS", "document_id": 2496, "error": ""}
    assert (await Fake(v2).task("x"))["document_id"] == 77
    assert await Fake(fail).task("x") == {"status": "FAILURE", "document_id": None, "error": "Duplikat"}
    assert await Fake({"results": []}).task("x") is None


def test_delete_document_from_review(client):
    FakePaperless.deleted.clear()
    db = client.app.state.db
    old = db.create_job(1, 42, "poll"); db.update_job(old, status="review", result={"fields": {}, "tags": [], "current": {}})
    job = db.create_job(1, 42, "manual"); db.update_job(job, status="review", result={"fields": {}, "tags": [], "current": {}})
    done = db.create_job(1, 43, "poll"); db.update_job(done, status="done")
    assert "Dokument löschen" in client.get("/review").text
    r = client.post(f"/jobs/{job}/delete-document", data={"back": f"/review?after={job}"}, follow_redirects=False)
    assert r.headers["location"].startswith(f"/review?after={job}&msg=")
    assert FakePaperless.deleted == [42]
    assert db.job(job)["status"] == "deleted" and db.job(old)["status"] == "deleted"
    assert db.job(done)["status"] == "done"  # anderes Dokument bleibt
    assert "Nichts zu prüfen" in client.get("/review").text


def test_create_delete_and_color_metadata(client):
    FakePaperless.objects.clear()
    cfg = client.app.state.config
    page = client.get("/descriptions?instance_id=1&kind=tag").text
    assert 'action="/meta/create"' in page and 'type="color"' in page and '/meta/delete' in page

    r = client.post("/meta/create", data={"instance_id": "1", "kind": "tag", "name": " Garten ", "color": "#33a02c", "keywords": "Garten, Pflanzen"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert FakePaperless.objects[-1] == ("create", "tag", "Garten", "#33a02c")
    oid = max(FakePaperless.meta.tags)
    assert cfg.descriptions(1)[("tag", oid)]["source"] == "Garten, Pflanzen"
    # gleicher Name nochmals -> Fehler, nichts angelegt
    r = client.post("/meta/create", data={"instance_id": "1", "kind": "tag", "name": "garten"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and len(FakePaperless.objects) == 1
    # Speicherpfade lassen sich hier nicht anlegen
    r = client.post("/meta/create", data={"instance_id": "1", "kind": "storage_path", "name": "x"}, follow_redirects=False)
    assert "err=" in r.headers["location"]

    r = client.post("/meta/color", data={"instance_id": "1", "tag_id": str(oid), "color": "#FF0000"}, follow_redirects=False)
    assert FakePaperless.objects[-1] == ("update", "tag", oid, {"color": "#ff0000"})
    assert "err=" in client.post("/meta/color", data={"instance_id": "1", "tag_id": str(oid), "color": "red"}, follow_redirects=False).headers["location"]

    r = client.post("/meta/delete", data={"instance_id": "1", "kind": "tag", "delete_id": str(oid)}, follow_redirects=False)
    assert FakePaperless.objects[-1] == ("delete", "tag", oid) and ("tag", oid) not in cfg.descriptions(1)
    # Posteingang (100) ist geschützt
    r = client.post("/meta/delete", data={"instance_id": "1", "kind": "tag", "delete_id": "100"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and FakePaperless.objects[-1][0] == "delete" and FakePaperless.objects[-1][2] == oid


def test_create_metadata_as_json_and_from_job_page(client):
    FakePaperless.objects.clear()
    r = client.post("/meta/create", data={"instance_id": "1", "kind": "correspondent", "name": "Neuer Absender", "format": "json"})
    assert r.status_code == 200 and r.json()["name"] == "Neuer Absender" and r.json()["kind"] == "correspondent"
    assert client.post("/meta/create", data={"instance_id": "1", "kind": "correspondent", "name": "neuer absender", "format": "json"}).status_code == 400
    r = client.post("/meta/create", data={"instance_id": "1", "kind": "document_type", "name": "Offerte", "back": "/jobs/7"}, follow_redirects=False)
    assert r.headers["location"].startswith("/jobs/7?msg=")


def test_new_entry_description_expanded_in_background(client, monkeypatch):
    import time

    async def fake_describe(llm, kind, name, notes, examples, lang="de"):
        return f"Ausformuliert: {notes}"

    monkeypatch.setattr(app_module, "describe_category", fake_describe)
    cfg = client.app.state.config
    cfg.update({"llm_provider": "ollama", "llm_url": "http://ollama:11434", "llm_model": "qwen3:8b"})
    r = client.post("/meta/create", data={"instance_id": "1", "kind": "document_type", "name": "Mahnung", "keywords": "Mahnung, Zahlungserinnerung", "format": "json"})
    oid = r.json()["id"]
    for _ in range(50):
        if cfg.descriptions(1)[("document_type", oid)]["text"].startswith("Ausformuliert"):
            break
        time.sleep(0.05)
    d = cfg.descriptions(1)[("document_type", oid)]
    assert d == {"text": "Ausformuliert: Mahnung, Zahlungserinnerung", "source": "Mahnung, Zahlungserinnerung", "active": True}


def test_review_shows_fallback_hint(client):
    db = client.app.state.db
    j = db.create_job(1, 42, "poll")
    db.update_job(j, status="review", doc_title="Ohne Absender", result={
        "fields": {"correspondent": {"value": 1, "label": "Swisscom", "confidence": 0.97, "level": "auto", "fallback": True, "top": []}},
        "tags": [], "current": {"tags": []}})
    page = client.get("/review").text
    assert "Sammel-Korrespondent" in page and "kein passender gefunden" in page


async def test_status_tag_created_once_even_in_parallel():
    import asyncio as aio
    from paperless_jev.paperless import Metadata, PaperlessError
    from paperless_jev.processor import Processor

    created = []

    class PL:
        async def create_tag(self, name):
            await aio.sleep(0.01)
            if name in created:
                raise PaperlessError("Object violates owner / name unique constraint")
            created.append(name)
            return 777

        async def metadata(self):
            return Metadata(tags={777: "ai-review"} if created else {})

    class Inst:
        id = 1

    proc = Processor.__new__(Processor)
    proc._meta, proc._examples, proc._tag_locks = {}, {}, {}
    metas = [Metadata(), Metadata()]  # zwei Jobs mit je eigenem, veraltetem Stand
    ids = await aio.gather(*(proc._tag(Inst(), PL(), m, "ai-review") for m in metas))
    assert ids == [777, 777] and created == ["ai-review"]

    # auch ohne Sperre (z. B. zwei Container) wird der vorhandene Tag verwendet statt abzubrechen
    proc2 = Processor.__new__(Processor)
    proc2._meta, proc2._examples, proc2._tag_locks = {}, {}, {}
    assert await proc2._tag(Inst(), PL(), Metadata(), "ai-review") == 777


def test_empty_review_keeps_navigation(client):
    page = client.get("/review").text
    assert "Nichts zu prüfen" in page and 'class="page-review"' not in page and 'href="/scan" role="button"' in page


def _nest(meta):
    meta.tags |= {200: "Haus", 201: "Unterhalt", 210: "Velo (Service)", 211: "Velo (Reifen)"}
    meta.tag_parents = {201: 200}


def test_child_tag_brings_parent_along(client):
    _nest(FakePaperless.meta)
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    job = client.app.state.db.one("SELECT id FROM jobs")
    page = client.get(f"/review?job={job['id']}").text
    assert '<span class="tpar">Haus</span>Unterhalt' in page
    client.post(f"/jobs/{job['id']}/apply", data={"action": "apply", "tags": ["201"]})
    _, data = FakePaperless.patches[-1]
    assert {200, 201} <= set(data["tags"])


def test_tag_parent_and_nesting_routes(client):
    FakePaperless.objects.clear()
    _nest(FakePaperless.meta)
    page = client.get("/descriptions?instance_id=1&kind=tag").text
    assert 'action="/meta/nest"' in page and 'style="--depth: 1"' in page
    # Zyklus wird abgelehnt
    r = client.post("/meta/parent", data={"instance_id": "1", "tag_id": "200", "parent": "201"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and not FakePaperless.objects
    r = client.post("/meta/parent", data={"instance_id": "1", "tag_id": "101", "parent": "200"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert FakePaperless.objects == [("update", "tag", 101, {"parent": 200}), ("add_tag", 200, [101])]

    FakePaperless.objects.clear()
    r = client.post("/meta/nest", data={"instance_id": "1", "parent_name": "Velo", "ids": ["210", "211"], "rename": "on"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    created = FakePaperless.objects[0]
    assert created[:3] == ("create", "tag", "Velo")
    parent = max(FakePaperless.meta.tags)
    assert ("update", "tag", 210, {"parent": parent, "name": "Service"}) in FakePaperless.objects
    assert FakePaperless.objects[-1] == ("add_tag", parent, [210, 211])
    assert FakePaperless.meta.tag_label(211) == "Velo › Reifen"


def test_delete_assistant_moves_documents_and_children(client):
    FakePaperless.objects.clear()
    _nest(FakePaperless.meta)
    FakePaperless.meta.tag_parents[200] = 101  # Steuerrelevant > Haus > Unterhalt
    # Dokument 42 hat Tag 103 -> Papierkorb führt zum Assistenten
    page = client.get("/descriptions?instance_id=1&kind=tag").text
    assert 'href="/meta/delete?instance_id=1&amp;kind=tag&amp;id=103"' in page
    page = client.get("/meta/delete?instance_id=1&kind=tag&id=103").text
    assert "scan_0042" in page and 'name="target"' in page
    page = client.get("/meta/delete?instance_id=1&kind=tag&id=200").text
    assert "Unterhalt" in page and "Steuern" in page  # Untertag rückt unter den Obertag
    # eigener Untertag als Ziel ist ungültig
    r = client.post("/meta/delete", data={"instance_id": "1", "kind": "tag", "delete_id": "200", "target": "201"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and not FakePaperless.objects
    r = client.post("/meta/delete", data={"instance_id": "1", "kind": "tag", "delete_id": "200", "target": "103"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert FakePaperless.objects == [("reassign", "tag", 200, 103), ("update", "tag", 201, {"parent": 101}), ("delete", "tag", 200)]

    FakePaperless.objects.clear()
    r = client.post("/meta/delete", data={"instance_id": "1", "kind": "correspondent", "delete_id": "2", "target": "1"}, follow_redirects=False)
    assert FakePaperless.objects == [("reassign", "correspondent", 2, 1), ("delete", "correspondent", 2)]


def test_log_shows_title_written_to_paperless(client):
    db = client.app.state.db
    job = db.create_job(1, 961, "poll")
    db.update_job(job, doc_title="scan-202610011250", status="running")
    db.update_job(job, status="done", applied={"title": "OBI Quittung Gartenmaterial", "tags": []})
    assert db.job(job)["doc_title"] == "OBI Quittung Gartenmaterial"


def test_title_generated_with_analysis(client):
    cfg = client.app.state.config
    cfg.update({"title_mode": "template", "title_template": "{correspondent} {document_type} {created:%Y-%m}"})
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    job = client.app.state.db.one("SELECT * FROM jobs")
    # Vorschlag schon vor dem Übernehmen da und im Review vorausgefüllt
    assert job["result"]["title"] == "Swisscom Rechnung 2026-03"
    page = client.get(f"/review?job={job['id']}").text
    assert 'value="Swisscom Rechnung 2026-03"' in page
    # Titelvorschlag mit im Review korrigiertem Korrespondenten
    r = client.post(f"/jobs/{job['id']}/title?format=json", data={"correspondent": "2", "document_type": "10"})
    assert r.json()["title"].startswith("Stadtwerke Rechnung")


def test_recheck_documents_with_tag(client):
    cfg = client.app.state.config
    cfg.update({"title_mode": "template", "title_template": "{correspondent} {document_type}"})
    assert 'action="/run/tag"' in client.get("/").text
    # Probelauf: schreibt nichts
    r = client.post("/run/tag", data={"target": "1:103", "dry_run": "true"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    _wait(client)
    job = client.app.state.db.one("SELECT * FROM jobs ORDER BY id DESC")
    assert job["source"] == "tag-test" and job["status"] == "dry_run" and not FakePaperless.patches
    assert "title" not in job["result"]  # bestehender Titel bleibt
    # echter Lauf mit neuen Titeln
    client.post("/run/tag", data={"target": "1:103", "titles": "true"})
    _wait(client)
    job = client.app.state.db.one("SELECT * FROM jobs ORDER BY id DESC")
    assert job["source"].startswith("tag+title") and job["result"]["title"] == "Swisscom Rechnung"
    assert "err=" in client.post("/run/tag", data={"target": ""}, follow_redirects=False).headers["location"]


def test_review_unticks_deactivated_tag(client):
    client.app.state.config.save_description(1, "tag", 103, "", False)
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    job = client.app.state.db.one("SELECT * FROM jobs")
    assert job["status"] == "review"
    page = client.get(f"/review?job={job['id']}").text
    assert 'name="keep_tags" value="103">' in page and "deaktiviert" in page
