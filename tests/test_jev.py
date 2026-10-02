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

    client, model = classifier(ollama_cfg())
    client._http._transport = httpx.MockTransport(handler)
    criteria = {f"opt {i}": None for i in range(60)} | {NONE_LABEL: "none"}
    questions = {"document_type": {"type": "choice", "instructions": "?", "criteria": criteria}}
    questions |= {f"tag:{i}": {"type": "noul", "instructions": "?"} for i in range(70)}
    async with client:
        resp = await client.ask("text", questions, model)
    assert model == "clef"
    assert resp["answers"]["document_type"]["choice"] == "opt 37"
    assert set(resp["answers"]) == set(questions)  # keine Hilfsfragen der Vorrunde
    # Vorrunde (3 Gruppen + 70 Tags = 73 Fragen -> 2 Anfragen) + Finale
    assert len(calls) == 3 and resp["usage"]["input_tokens"] == 30


def test_classifier_missing():
    assert classifier_missing(ollama_cfg()) is None
    assert classifier_missing({**ollama_cfg(), "classifier_url": ""})
    assert classifier_missing({"classifier": "typesafe", "typesafe_api_key": ""})
