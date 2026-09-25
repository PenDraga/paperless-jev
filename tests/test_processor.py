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
