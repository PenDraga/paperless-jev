"""Neue Korrespondenten: Sprachmodell schlägt vor, Jev prüft, angelegt wird erst im Review (nach #2)."""

from __future__ import annotations

import copy
import json

import httpx
import pytest

from paperless_jev import i18n
from paperless_jev import processor as processor_module
from paperless_jev.classifier import MAX_CHOICE_OPTIONS, NONE
from paperless_jev.correspondents import NEW_CRITERION, clean_name, existing, with_candidate

from .test_classifier import jev_response
from .test_llm import _patch
from .test_processor import FakePaperless, _wait, client  # noqa: F401

NAME = "Velowerkstatt Beispiel GmbH"


class TwoRounds:
    """Jev-Attrappe: erste Anfrage "keiner passt", in der Gegenprüfung gewinnt der neue Name."""

    asked: list[dict] = []
    second: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def ask(self, state, questions, model):
        self.asked.append(questions)
        if len(self.asked) > 1:
            return copy.deepcopy(self.second)
        response = jev_response()
        response["answers"]["correspondent"] = {
            "type": "choice", "choice": NONE, "confidence": 0.9,
            "probabilities": {NONE: 0.9, "Swisscom": 0.07, "Stadtwerke": 0.03}}
        return response


@pytest.fixture
def proposing(client, monkeypatch):  # noqa: F811
    """Vorschläge eingeschaltet; das Sprachmodell nennt NAME, Jev bestätigt ihn."""
    TwoRounds.asked = []
    TwoRounds.second = {"model": "jev-1.13.0", "usage": {"input_tokens": 300}, "answers": {"correspondent": {
        "type": "choice", "choice": NAME, "confidence": 0.83,
        "probabilities": {NAME: 0.83, NONE: 0.1, "Swisscom": 0.07}}}}
    monkeypatch.setattr(processor_module, "classifier", lambda cfg, timeout=None: (TwoRounds(), cfg["model"]))
    monkeypatch.setattr(FakePaperless, "objects", [])
    FakePaperless.docs[42]["content"] = f"{NAME}\nRechnung vom 12.03.2026\nTotal CHF 84.20"
    client.app.state.config.update({"suggest_correspondents": True, "llm_url": "http://ollama:11434", "llm_model": "m"})
    client.prompts = []

    def llm(request: httpx.Request) -> httpx.Response:
        client.prompts.append(json.loads(request.read())["prompt"])
        if client.llm_answer is None:
            return httpx.Response(500, text="private-provider-detail")
        return httpx.Response(200, json={"response": client.llm_answer})

    client.llm_answer = json.dumps({"name": f"  {NAME} "})
    _patch(monkeypatch, llm)
    return client


def run(c) -> dict:
    c.post("/run", data={"instance_id": "0"})
    _wait(c)
    return c.app.state.db.one("SELECT * FROM jobs ORDER BY id DESC")


def created() -> list[tuple]:
    return [o for o in FakePaperless.objects if o[0] == "create"]


def test_proposal_is_reviewed_and_only_created_on_apply(proposing):
    job = run(proposing)
    assert job["status"] == "review", job["error"]
    field = job["result"]["fields"]["correspondent"]
    assert (field["new_name"], field["value"], field["level"], field["confidence"]) == (NAME, None, "suggest", 0.83)
    assert job["input_tokens"] == 800  # beide Anfragen an Jev zählen
    # das Sprachmodell kennt die vorhandenen, die Jev am ehesten für den Absender hält
    assert NAME in proposing.prompts[0] and '["Swisscom", "Stadtwerke"]' in proposing.prompts[0]
    # Gegenprüfung: nur die Korrespondenten-Frage, der neue Name vor "keiner davon"
    first, second = TwoRounds.asked
    assert list(second) == ["correspondent"]
    assert list(second["correspondent"]["criteria"])[-2:] == [NAME, NONE]
    assert second["correspondent"]["criteria"][NAME] == NEW_CRITERION.format(name=NAME)
    assert NAME not in first["correspondent"]["criteria"]
    # bis zum Übernehmen bleibt Paperless unverändert
    assert not created()
    assert "correspondent" not in FakePaperless.patches[-1][1]

    review = proposing.get("/review").text
    assert 'id="in-correspondent" value="new"' in review
    assert f'<span id="lbl-correspondent">{NAME}</span>' in review
    assert 'id="new-correspondent"' in review and "kein passender gefunden" not in review
    assert f'<option value="new" selected>«{NAME}» neu in Paperless anlegen</option>' in proposing.get(f"/jobs/{job['id']}").text
    assert f"neu: {NAME}" in proposing.get("/log?status=all").text

    proposing.post(f"/jobs/{job['id']}/apply", data={"action": "apply", "use_correspondent": "on", "correspondent": "new"})
    assert created() == [("create", "correspondent", NAME, None)]
    oid = existing(FakePaperless.meta.correspondents, NAME)
    assert FakePaperless.patches[-1][1]["correspondent"] == oid
    done = proposing.app.state.db.job(job["id"])
    assert done["status"] == "done" and "correspondent" not in done["applied"]["corrections"]


def test_another_choice_in_review_creates_nothing(proposing):
    job = run(proposing)
    proposing.post(f"/jobs/{job['id']}/apply", data={"action": "apply", "use_correspondent": "on", "correspondent": "2"})
    assert not created()
    assert FakePaperless.patches[-1][1]["correspondent"] == 2
    assert proposing.app.state.db.job(job["id"])["applied"]["corrections"]["correspondent"] == {"suggested": None, "chosen": 2}


def test_same_name_created_meanwhile_is_reused(proposing):
    job = run(proposing)
    FakePaperless.meta.correspondents[77] = NAME.upper()
    review = proposing.get("/review").text
    assert 'id="in-correspondent" value="77"' in review and 'id="new-correspondent"' not in review
    # auch ein noch offenes Formular mit "new" legt kein Duplikat an
    proposing.post(f"/jobs/{job['id']}/apply", data={"action": "apply", "use_correspondent": "on", "correspondent": "new"})
    assert not created()
    assert FakePaperless.patches[-1][1]["correspondent"] == 77


def test_proposed_name_goes_into_the_title(proposing):
    proposing.app.state.config.update({"title_mode": "template", "title_template": "{document_type} {correspondent}"})
    job = run(proposing)
    assert job["result"]["title"] == f"Rechnung {NAME}"
    data = {"correspondent": "new", "document_type": "10"}
    assert proposing.post(f"/jobs/{job['id']}/title?format=json", data=data).json() == {"title": f"Rechnung {NAME}"}
    # im Review ein anderer gewählt: dessen Name zählt
    data["correspondent"] = "2"
    assert proposing.post(f"/jobs/{job['id']}/title?format=json", data=data).json() == {"title": "Rechnung Stadtwerke"}


@pytest.mark.parametrize("case", [
    "disabled", "no_llm", "assigned", "confident", "null", "no_json", "too_long", "control_chars",
    "exists", "same_as_known", "is_candidate", "jev_prefers_existing", "below_review_threshold",
])
def test_no_proposal(proposing, monkeypatch, case):
    cfg = proposing.app.state.config
    llm_calls, jev_calls = 1, 2
    if case == "disabled":
        cfg.update({"suggest_correspondents": False})
        llm_calls, jev_calls = 0, 1
    elif case == "no_llm":
        cfg.update({"llm_model": ""})
        llm_calls, jev_calls = 0, 1
    elif case == "assigned":
        FakePaperless.docs[42]["correspondent"] = 2
        llm_calls, jev_calls = 0, 1
    elif case == "confident":
        async def confident(self, state, questions, model):
            self.asked.append(questions)
            response = jev_response()
            response["answers"]["correspondent"]["confidence"] = 0.99
            return response

        monkeypatch.setattr(TwoRounds, "ask", confident)
        llm_calls, jev_calls = 0, 1
    elif case in ("null", "no_json", "too_long", "control_chars", "exists", "same_as_known", "is_candidate"):
        proposing.llm_answer = {
            "null": '{"name": null}',
            "no_json": "I could not find a sender.",
            "too_long": json.dumps({"name": "x" * 129}),
            "control_chars": json.dumps({"name": "Beispiel\nBahnhofstrasse 1"}),
            "exists": '{"name": "stadtwerke "}',
            "same_as_known": json.dumps({"name": "Swisscom (Schweiz) AG", "same_as": "swisscom"}),
            "is_candidate": json.dumps({"name": NONE}),
        }[case]
        jev_calls = 1
    elif case == "jev_prefers_existing":
        TwoRounds.second["answers"]["correspondent"].update(choice="Swisscom", confidence=0.7)
    elif case == "below_review_threshold":
        TwoRounds.second["answers"]["correspondent"]["confidence"] = 0.39

    job = run(proposing)
    assert job["status"] in ("review", "done"), job["error"]
    assert "new_name" not in job["result"]["fields"]["correspondent"]
    assert (len(proposing.prompts), len(TwoRounds.asked)) == (llm_calls, jev_calls)
    assert 'value="new"' not in proposing.get(f"/jobs/{job['id']}").text
    assert not created()


@pytest.mark.parametrize("failing", ["llm", "jev"])
def test_failed_proposal_keeps_the_first_result(proposing, monkeypatch, caplog, failing):
    monkeypatch.setattr(i18n, "LOG_LANGUAGE", "de")
    if failing == "llm":
        proposing.llm_answer = None
    else:
        first = TwoRounds.ask

        async def second_fails(self, state, questions, model):
            if self.asked:
                raise processor_module.JevError("Jev HTTP 429")
            return await first(self, state, questions, model)

        monkeypatch.setattr(TwoRounds, "ask", second_fails)
    job = run(proposing)
    assert job["status"] == "review", job["error"]
    assert job["result"]["fields"]["correspondent"]["level"] == "none"
    assert "Neuer Korrespondent für Dokument 42 nicht vorgeschlagen" in caplog.text
    assert len(proposing.prompts) == 1 and len(TwoRounds.asked) == 1


def test_new_without_a_proposal_is_rejected(client):  # noqa: F811
    FakePaperless.objects = []
    job = run(client)
    r = client.post(f"/jobs/{job['id']}/apply", follow_redirects=False,
                    data={"action": "apply", "use_correspondent": "on", "correspondent": "new"})
    assert r.status_code == 303 and r.headers["location"].startswith(f"/jobs/{job['id']}")
    assert "kein neuer Korrespondent vorgeschlagen" in client.get(r.headers["location"]).text
    assert not created()
    assert client.app.state.db.job(job["id"])["status"] == "review"
    # nur der Korrespondent kennt "new"
    with pytest.raises(ValueError):
        client.post(f"/jobs/{job['id']}/apply", data={"action": "apply", "use_document_type": "on", "document_type": "new"})


def test_rules_save_the_option(client):  # noqa: F811
    cfg = client.app.state.config
    assert cfg.all()["suggest_correspondents"] is False
    client.post("/rules", data={"suggest_correspondents": "on"})
    assert cfg.all()["suggest_correspondents"] is True
    rules = client.get("/rules").text
    assert 'name="suggest_correspondents" checked' in rules and 'href="/setup#language-model"' in rules
    client.post("/rules", data={})
    assert cfg.all()["suggest_correspondents"] is False


def test_same_as_counts_only_for_known_names(proposing):
    proposing.llm_answer = json.dumps({"name": NAME, "same_as": "Erfunden AG"})
    assert run(proposing)["result"]["fields"]["correspondent"]["new_name"] == NAME


def test_names_from_the_language_model_are_cleaned():
    assert clean_name('  "Beispiel   AG" ') == "Beispiel AG"
    assert clean_name("«Beispiel AG»") == "Beispiel AG"
    for bad in (None, 7, "", "  ", "x" * 129, "Beispiel\x00AG", "Beispiel\nAG"):
        assert clean_name(bad) is None
    assert existing({1: "Beispiel  AG", 2: "Andere"}, " beispiel ag") == 1
    assert existing({1: "Beispiel AG"}, "Beispiel") is None


def test_candidate_respects_the_option_limit():
    question = {"type": "choice", "instructions": "?", "criteria": {**{f"c{i}": "x" for i in range(MAX_CHOICE_OPTIONS)}, NONE: "none"}}
    criteria = with_candidate(question, NAME)["criteria"]
    assert len(criteria) == MAX_CHOICE_OPTIONS + 1
    assert list(criteria)[-2:] == [NAME, NONE] and "c0" in criteria and f"c{MAX_CHOICE_OPTIONS - 1}" not in criteria
    assert len(question["criteria"]) == MAX_CHOICE_OPTIONS + 1 and NAME not in question["criteria"]
