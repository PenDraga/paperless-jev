"""Ollama/clef: höchstens 64 Fragen und 26 Kandidaten pro Auswahlfrage."""

from __future__ import annotations

import httpx
import pytest

from paperless_jev.jev import NONE_LABEL, classifier, classifier_missing


def ollama_cfg() -> dict:
    return {"classifier": "ollama", "classifier_url": "http://ollama:11434", "classifier_model": "clef", "typesafe_api_key": ""}


@pytest.mark.asyncio
async def test_tournament_and_chunking():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        calls.append(body)
        assert len(body["questions"]) <= 64
        if any(len(q.get("criteria") or {}) > 26 for q in body["questions"].values()):
            return httpx.Response(400, json={"error": 'question "document_type": criteria must contain 2–26 candidates'})
        answers = {}
        for key, q in body["questions"].items():
            if q["type"] == "noul":
                answers[key] = {"type": "noul", "noul": 0.9}
                continue
            labels = list(q["criteria"])
            assert 2 <= len(labels) <= 26
            # "opt 37" gewinnt immer, sonst gleichmässig
            probs = {lbl: (0.9 if lbl == "opt 37" else 0.1 / len(labels)) for lbl in labels}
            best = max(probs, key=probs.get)
            answers[key] = {"type": "choice", "choice": best, "probabilities": probs, "confidence": 0.5}
        return httpx.Response(200, json={"model": "clef", "answers": answers, "usage": {"input_tokens": 10}})

    client, model = classifier({**ollama_cfg(), "classifier_url": "http://clef-like:11434"})
    client._http._transport = httpx.MockTransport(handler)
    criteria = {f"opt {i}": None for i in range(60)} | {NONE_LABEL: "none"}
    questions = {"document_type": {"type": "choice", "instructions": "?", "criteria": criteria}}
    questions |= {f"tag:{i}": {"type": "noul", "instructions": "?"} for i in range(70)}
    async with client:
        resp = await client.ask("text", questions, model)
    assert model == "clef"
    assert resp["answers"]["document_type"]["choice"] == "opt 37"
    assert set(resp["answers"]) == set(questions)  # keine Hilfsfragen der Vorrunde
    # erst ohne Limit (abgelehnt), dann Vorrunde (3 Gruppen + 70 Tags = 73 Fragen -> 2 Anfragen) + Finale
    assert len(calls) == 4 and resp["usage"]["input_tokens"] == 30
    # das Limit wird pro Server gemerkt: kein zweiter Versuch ohne Turnier
    calls.clear()
    async with classifier({**ollama_cfg(), "classifier_url": "http://clef-like:11434"})[0] as again:
        again._http._transport = httpx.MockTransport(handler)
        await again.ask("text", questions, model)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_server_without_candidate_limit_gets_no_tournament():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        calls.append(body)
        q = body["questions"]["document_type"]
        return httpx.Response(200, json={"answers": {"document_type": {
            "type": "choice", "choice": "opt 7", "confidence": 0.9, "probabilities": {k: 0.0 for k in q["criteria"]}}}})

    client, model = classifier({**ollama_cfg(), "classifier_url": "http://lux:8020"})
    client._http._transport = httpx.MockTransport(handler)
    criteria = {f"opt {i}": None for i in range(200)} | {NONE_LABEL: "none"}
    async with client:
        resp = await client.ask("text", {"document_type": {"type": "choice", "instructions": "?", "criteria": criteria}}, model)
    assert len(calls) == 1 and len(calls[0]["questions"]["document_type"]["criteria"]) == 201
    assert resp["answers"]["document_type"]["choice"] == "opt 7"


def test_classifier_missing():
    assert classifier_missing(ollama_cfg()) is None
    assert classifier_missing({**ollama_cfg(), "classifier_url": ""})
    assert classifier_missing({"classifier": "typesafe", "typesafe_api_key": ""})
