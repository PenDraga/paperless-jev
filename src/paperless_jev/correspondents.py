"""Neue Korrespondenten vorschlagen (nach #2).

Erkennt Jev keinen vorhandenen Korrespondenten sicher, liest das Sprachmodell den
Absender aus dem Dokument. Jev prüft diesen Namen danach als zusätzliche Auswahl
gegen alle vorhandenen. Setzt er sich durch, steht er als Vorschlag im Review -
angelegt wird in Paperless erst beim Übernehmen.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .classifier import MAX_CHOICE_OPTIONS, NONE, prepare_text
from .llm import LLM, complete

NEW = "new"  # Formularwert im Review: vorgeschlagenen Korrespondenten anlegen
NO_PROPOSAL = "Für dieses Dokument ist kein neuer Korrespondent vorgeschlagen"
MAX_NAME = 128  # Limit von Paperless
TEXT_CHARS = 4000  # Briefkopf und Fusszeile reichen für den Absender
# Der Name muss im Kriterium stehen: ohne ihn hält Jev auch den Empfänger für einen neuen Absender.
NEW_CRITERION = "The document was sent or issued by {name}, a sender that is not among the other options."
PROMPT = """Name the sender of the document below, so that it can be filed under that name.

- The sender is the company, authority or person that sent or issued the document - never the recipient.
- Write the name as it is printed (letterhead, footer, signature), without address, department or slogan.
- Keep a legal form such as AG, GmbH or Ltd only if it is part of the usual name.
- If the sender is not clearly named, answer null instead of guessing.
{known}
Answer with JSON only: {answer}

Document:
{text}"""
# Vorhandene Namen mitgeben, damit aus "Beispiel (Schweiz) AG" kein zweites "Beispiel" wird. Das
# Sprachmodell nennt den Namen wie gedruckt und sagt getrennt davon, ob er schon vorhanden ist.
KNOWN = """
Already on file are: {names}
If one of them is the very same company, authority or person as the sender, only written shorter or \
slightly differently, give that name as "same_as". A different sender from the same line of business \
or the same group is not the same: then "same_as" is null.
"""
ANSWER = '{"name": "..."} or {"name": null}'
ANSWER_KNOWN = '{"name": "...", "same_as": "..."}, {"name": "...", "same_as": null} or {"name": null, "same_as": null}'


def proposed_name(result: dict[str, Any] | None) -> str | None:
    """Name des vorgeschlagenen neuen Korrespondenten in einem Job-Ergebnis."""
    return ((result or {}).get("fields", {}).get("correspondent") or {}).get("new_name")


def existing(names: dict[int, str], name: str) -> int | None:
    """ID eines Korrespondenten gleichen Namens - Gross-/Kleinschreibung und Leerzeichen egal."""
    wanted = " ".join(name.split()).casefold()
    return next((oid for oid, n in names.items() if " ".join(n.split()).casefold() == wanted), None)


def likely(field: dict[str, Any] | None) -> list[str]:
    """Vorhandene Korrespondenten, die Jev laut einem Feld-Ergebnis am ehesten für den Absender hält."""
    return [label for label, _ in (field or {}).get("top", []) if label != NONE]


def clean_name(raw: Any) -> str | None:
    """Antwort des Sprachmodells als Name - oder None, wenn sie sich nicht als Name eignet."""
    if not isinstance(raw, str) or re.search(r"[\x00-\x1f\x7f]", raw):
        return None
    name = " ".join(raw.split()).strip("\"'«»„“”")
    return name if 0 < len(name) <= MAX_NAME else None


async def propose(llm: LLM, content: str, known: list[str]) -> str | None:
    """Absender laut Sprachmodell; None, wenn es keinen nennt oder die Antwort unbrauchbar ist.

    known: vorhandene Korrespondenten, die Jev am ehesten für den Absender hält. Erkennt das
    Sprachmodell einen davon wieder, gibt es nichts vorzuschlagen.
    """
    text = prepare_text(content, TEXT_CHARS)
    if not text:
        return None
    prompt = PROMPT.format(
        text=text,
        known=KNOWN.format(names=json.dumps(known, ensure_ascii=False)) if known else "",
        answer=ANSWER_KNOWN if known else ANSWER,
    )
    answer = await complete(llm, prompt, max_tokens=120, temperature=0.0)
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    same = clean_name(data.get("same_as"))
    if same and existing(dict(enumerate(known)), same) is not None:
        return None  # schon vorhanden, nur anders geschrieben
    return clean_name(data.get("name"))


def with_candidate(question: dict[str, Any], name: str) -> dict[str, Any]:
    """Die Korrespondenten-Frage mit dem neuen Namen als zusätzlicher Auswahl."""
    criteria = {label: c for label, c in question["criteria"].items() if label != NONE}
    if len(criteria) >= MAX_CHOICE_OPTIONS:
        criteria.popitem()  # API-Limit: der unwahrscheinlichste Kandidat macht Platz
    return {**question, "criteria": {**criteria, name: NEW_CRITERION.format(name=name), NONE: question["criteria"][NONE]}}


def accepted(answer: dict[str, Any], name: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    """Feld-Ergebnis, wenn Jev den neuen Namen allen vorhandenen vorzieht - sonst None.

    Die Stufe ist immer "suggest": ein neuer Korrespondent braucht die Freigabe im Review.
    """
    confidence = float(answer.get("confidence", 0.0))
    if answer.get("choice") != name or confidence < float(spec["review"]):
        return None
    probs = sorted(answer.get("probabilities", {}).items(), key=lambda kv: kv[1], reverse=True)
    return {
        "value": None,
        "new_name": name,
        "label": name,
        "confidence": round(confidence, 4),
        "level": "suggest",
        "top": [[label, round(p, 4)] for label, p in probs[:5]],
    }
