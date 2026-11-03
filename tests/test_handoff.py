"""Übergabe von einem vorgelagerten Werkzeug: Warteschlangen-Tag und System-Tags."""

from __future__ import annotations

import copy

import pytest

from paperless_jev.classifier import build_request, excluded_tags, interpret, plan
from paperless_jev.config import DEFAULTS
from paperless_jev.jev import classifier
from paperless_jev.paperless import Metadata

from .test_processor import FakePaperless, _wait, client  # noqa: F401

PENDING = 90


@pytest.mark.parametrize("present", [False, True])
def test_system_tags_are_never_suggested_or_checked(present):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["system_tags"] = ["paperless-gpt-complete"]
    cfg["verify_tags"] = False
    for name, spec in cfg["fields"].items():
        spec["enabled"] = name == "tags"
    meta = Metadata(tags={17: "hosting", 18: "paperless-gpt-complete"})
    doc = {"id": 1, "content": "Server hosting invoice", "tags": [18] if present else []}
    req = build_request(doc, meta, {("tag", 18): {"active": not present}}, cfg)
    assert set(req.questions) == {"tag:17"}
    result = interpret({"answers": {"tag:17": {"type": "noul", "noul": 0.98}}}, req, meta, cfg)
    assert plan(result, doc, cfg) == {"updates": {"tags_add": [17]}, "needs_review": False}


def test_queue_tag_is_excluded():
    meta = Metadata(tags={PENDING: "jev-pending", 17: "hosting"})
    assert PENDING in excluded_tags(meta, {**DEFAULTS, "poll_tag": "jev-pending"})
    assert PENDING not in excluded_tags(meta, DEFAULTS)


def _handoff(client):  # noqa: F811
    client.app.state.config.update({"poll_tag": "jev-pending"})
    FakePaperless.meta.tags[PENDING] = "jev-pending"
    client.app.state.processor.forget_metadata()


def test_polling_takes_only_handed_over_documents(client):  # noqa: F811
    _handoff(client)
    FakePaperless.docs[43] = {**FakePaperless.docs[42], "id": 43, "tags": [100, PENDING]}
    client.post("/run", data={"instance_id": "0"})
    _wait(client)
    assert [row["doc_id"] for row in client.app.state.db.query("SELECT doc_id FROM jobs")] == [43]
    # Tag nach der Bearbeitung entfernt, andere Tags bleiben
    tags = FakePaperless.docs[43]["tags"]
    assert PENDING not in tags and 100 in tags


def test_webhook_waits_for_queue_tag(client):  # noqa: F811
    _handoff(client)
    secret = client.app.state.config.all()["webhook_secret"]
    r = client.post(f"/hook/home?token={secret}", data={"doc_id": "42"}).json()
    assert r["queued"] is False
    FakePaperless.docs[42]["tags"].append(PENDING)
    assert client.post(f"/hook/home?token={secret}", data={"doc_id": "42"}).json()["queued"] is True
    _wait(client)
    assert PENDING not in FakePaperless.docs[42]["tags"]


def test_rules_save_queue_and_system_tags(client):  # noqa: F811
    form = {"mode": "dry_run", "poll_tag": " jev-pending ", "system_tags": "gpt-done, ocr-complete,,gpt-done "}
    assert client.post("/rules", data=form).status_code == 200
    cfg = client.app.state.config.all()
    assert cfg["poll_tag"] == "jev-pending"
    assert cfg["system_tags"] == ["gpt-done", "ocr-complete"]
    assert 'value="gpt-done, ocr-complete"' in client.get("/rules").text


def test_dashboard_warning_follows_selected_classifier(client):  # noqa: F811
    cfg = client.app.state.config
    cfg.update({"typesafe_api_key": "", "classifier": "ollama", "classifier_url": "http://ollama:11434"})
    assert "TypeSafe-API-Key" not in client.get("/").text
    cfg.update({"classifier": "typesafe"})
    assert "Kein TypeSafe-API-Key konfiguriert" in client.get("/").text


def test_compatible_server_api_key(client):  # noqa: F811
    client.post("/setup/typesafe", data={"classifier_kind": "ollama", "classifier_url": "http://lux:8020",
                                         "classifier_model": "lux", "classifier_api_key": "s3cret"})
    cfg = client.app.state.config.all()
    assert cfg["classifier_api_key"] == "s3cret"
    jev, _ = classifier(cfg)
    assert jev._http.headers["Authorization"] == "Bearer s3cret"
    # leeres Feld behält den Key
    client.post("/setup/typesafe", data={"classifier_kind": "ollama", "classifier_url": "http://lux:8020", "classifier_model": "lux"})
    assert client.app.state.config.all()["classifier_api_key"] == "s3cret"
    assert "s3cret" not in client.get("/setup").text
