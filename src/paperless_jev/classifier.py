"""Baut die Jev-Anfrage für ein Dokument und wertet die Antwort aus.

Reine Funktionen ohne I/O, damit sie sich ohne Paperless und TypeSafe testen
lassen. Die Fragen sind bewusst englisch formuliert: Jev ist primär auf
Englisch trainiert, der Dokumenttext selbst darf deutsch bleiben.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .candidates import correspondent_candidates, find_dates
from .config import SINGLE_FIELDS
from .paperless import Metadata

NONE = "(none of these)"
NONE_CRITERION = "None of the listed options fits this document."
MAX_CHOICE_OPTIONS = 254  # API-Limit 255, eins bleibt für NONE
MAX_TAG_QUESTIONS = 120

INSTRUCTIONS = {
    "document_type": "What type of document is this?",
    "correspondent": (
        "Who is the correspondent of this document, i.e. the company, authority or "
        "person that sent or issued it (not the recipient)?"
    ),
    "storage_path": "In which of these storage locations should this document be filed?",
    "created": (
        "Which of these dates is the issue date of the document, i.e. the date the "
        "letter, invoice or statement was written? Not a due date, delivery date, "
        "service period, date of birth or a date mentioned in passing."
    ),
}
TAG_INSTRUCTION = 'Does the tag "{name}" apply to this document?'
CONVENTION_HINT = (
    " Follow the owner's filing conventions: each option lists titles of documents "
    "that are already filed under it."
)
EXAMPLES_KEY = "titles_of_documents_already_filed_here"

Examples = dict[tuple[str, int], list[str]]

KIND_OF_FIELD = {
    "document_type": "document_type",
    "correspondent": "correspondent",
    "storage_path": "storage_path",
}


@dataclass
class JevRequest:
    state: dict[str, Any]
    questions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Frage-Key -> Optionslabel -> Wert (Paperless-ID bzw. ISO-Datum)
    options: dict[str, dict[str, Any]] = field(default_factory=dict)


def prepare_text(content: str, max_chars: int) -> str:
    """Kürzt lange Texte auf Anfang und Ende - dort stehen Absender, Datum, Summen."""
    text = "\n".join(line.rstrip() for line in (content or "").splitlines() if line.strip())
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.7)
    tail = max_chars - head
    return f"{text[:head]}\n[...]\n{text[-tail:]}"


def _labels(items: dict[int, str]) -> dict[str, int]:
    """Eindeutige Optionslabels; doppelte Namen bekommen die ID angehängt."""
    counts: dict[str, int] = {}
    for name in items.values():
        counts[name] = counts.get(name, 0) + 1
    labels: dict[str, int] = {}
    for oid, name in items.items():
        label = name if counts[name] == 1 and name != NONE else f"{name} #{oid}"
        labels[label] = oid
    return labels


def _active(
    kind: str, items: dict[int, str], descriptions: dict[tuple[str, int], dict[str, Any]]
) -> dict[int, str]:
    return {
        oid: name
        for oid, name in items.items()
        if descriptions.get((kind, oid), {}).get("active", True)
    }


def _criterion(kind: str, oid: int, descriptions: dict, examples: Examples) -> Any:
    """Beschreibung und/oder Beispieltitel als (strukturiertes) Kriterium."""
    text = descriptions.get((kind, oid), {}).get("text", "")
    titles = examples.get((kind, oid))
    if titles:
        return {"what": text, EXAMPLES_KEY: titles} if text else {EXAMPLES_KEY: titles}
    return text or None


def excluded_tags(meta: Metadata, cfg: dict[str, Any]) -> set[int]:
    """Tags, die Jev nie vorschlagen soll: Posteingang und die eigenen Status-Tags."""
    ids = set(meta.inbox_tags)
    for key in ("tag_done", "tag_review", "tag_ignore"):
        if (tid := meta.tag_id(cfg[key])) is not None:
            ids.add(tid)
    return ids


def build_request(
    doc: dict[str, Any],
    meta: Metadata,
    descriptions: dict[tuple[str, int], dict[str, Any]],
    cfg: dict[str, Any],
    similar: list[dict[str, Any]] | None = None,
    examples: Examples | None = None,
) -> JevRequest:
    examples = examples or {}
    text = prepare_text(doc.get("content", ""), int(cfg["max_chars"]))
    state: dict[str, Any] = {
        "document": {"file_name": doc.get("original_file_name") or "", "text": text}
    }
    if similar:
        state["similar_documents_already_filed"] = [
            {
                "title": s.get("title"),
                "document_type": meta.document_types.get(s.get("document_type")),
                "correspondent": meta.correspondents.get(s.get("correspondent")),
                "tags": [meta.tags[t] for t in s.get("tags", []) if t in meta.tags],
            }
            for s in similar
        ]
    req = JevRequest(state=state)
    fields = cfg["fields"]

    for name, kind in KIND_OF_FIELD.items():
        if not fields[name]["enabled"]:
            continue
        items = _active(kind, meta.names(kind), descriptions)
        if name == "correspondent":
            preferred = {s["correspondent"] for s in similar or [] if s.get("correspondent")}
            items = correspondent_candidates(text, items, preferred, limit=MAX_CHOICE_OPTIONS)
        if not items:
            continue
        labels = dict(list(_labels(items).items())[:MAX_CHOICE_OPTIONS])
        criteria: dict[str, Any] = {
            label: _criterion(kind, oid, descriptions, examples) for label, oid in labels.items()
        }
        criteria[NONE] = NONE_CRITERION
        instructions = INSTRUCTIONS[name]
        if any(isinstance(c, dict) for c in criteria.values()):
            instructions += CONVENTION_HINT
        req.questions[name] = {
            "type": "choice",
            "instructions": instructions,
            "criteria": criteria,
        }
        req.options[name] = {**labels, NONE: None}

    if fields["created"]["enabled"]:
        dates = find_dates(text)
        if dates:
            criteria = {d.label: f"Context: ...{d.context}..." for d in dates}
            criteria[NONE] = "The document states no issue date."
            req.questions["created"] = {
                "type": "choice",
                "instructions": INSTRUCTIONS["created"],
                "criteria": criteria,
            }
            req.options["created"] = {d.label: d.value.isoformat() for d in dates}
            req.options["created"][NONE] = None

    if fields["tags"]["enabled"]:
        skip = excluded_tags(meta, cfg) | set(doc.get("tags", []))
        tags = {tid: n for tid, n in _active("tag", meta.tags, descriptions).items() if tid not in skip}
        for tid, tag_name in list(tags.items())[:MAX_TAG_QUESTIONS]:
            question: dict[str, Any] = {
                "type": "noul",
                "instructions": TAG_INSTRUCTION.format(name=tag_name),
            }
            if desc := _criterion("tag", tid, descriptions, examples):
                question["criteria"] = {"true": desc, "false": f'The tag "{tag_name}" does not apply.'}
            req.questions[f"tag:{tid}"] = question

    return req


def _level(confidence: float, spec: dict[str, Any]) -> str:
    if confidence >= float(spec["auto"]):
        return "auto"
    if confidence >= float(spec["review"]):
        return "suggest"
    return "low"


def interpret(
    response: dict[str, Any], req: JevRequest, meta: Metadata, cfg: dict[str, Any]
) -> dict[str, Any]:
    answers = response.get("answers", {})
    result: dict[str, Any] = {
        "model": response.get("model"),
        "usage": response.get("usage", {}),
        "fields": {},
        "tags": [],
    }
    for name in SINGLE_FIELDS:
        answer = answers.get(name)
        if not answer:
            continue
        label = answer.get("choice")
        value = req.options[name].get(label)
        confidence = float(answer.get("confidence", 0.0))
        probs = sorted(
            answer.get("probabilities", {}).items(), key=lambda kv: kv[1], reverse=True
        )
        result["fields"][name] = {
            "value": value,
            "label": label,
            "confidence": round(confidence, 4),
            "level": "none" if value is None else _level(confidence, cfg["fields"][name]),
            "top": [[lbl, round(p, 4)] for lbl, p in probs[:5]],
        }
    tag_spec = cfg["fields"]["tags"]
    for key, answer in answers.items():
        if not key.startswith("tag:"):
            continue
        tid = int(key[4:])
        p = float(answer.get("noul", 0.0))
        result["tags"].append(
            {"id": tid, "label": meta.tags.get(tid, str(tid)), "p": round(p, 4), "level": _level(p, tag_spec)}
        )
    result["tags"].sort(key=lambda t: t["p"], reverse=True)
    return result


def plan(result: dict[str, Any], doc: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Welche Werte sicher genug zum Setzen sind und ob ein Mensch draufschauen muss."""
    updates: dict[str, Any] = {}
    needs_review = False
    for name, spec in cfg["fields"].items():
        if name == "tags" or not spec["enabled"]:
            continue
        found = result["fields"].get(name)
        current = doc.get(name)
        if name == "created":
            # Paperless setzt immer ein Datum; nur korrigieren, wenn Jev sicher ist.
            if found and found["value"] and found["value"] != current:
                if found["level"] == "auto":
                    updates[name] = found["value"]
                elif found["level"] == "suggest":
                    needs_review = True
            continue
        if current and not cfg["overwrite"]:
            continue
        if found and found["level"] == "auto" and found["value"] != current:
            updates[name] = found["value"]
        elif not current:
            needs_review = True
    if cfg["fields"]["tags"]["enabled"]:
        updates["tags_add"] = [t["id"] for t in result["tags"] if t["level"] == "auto"]
        if cfg.get("tags_force_review") and any(t["level"] == "suggest" for t in result["tags"]):
            needs_review = True
    return {"updates": updates, "needs_review": needs_review}


class _Strict(dict):
    def __missing__(self, key: str) -> Any:
        raise KeyError(key)


def render_title(template: str, values: dict[str, Any]) -> str | None:
    """Titel aus Vorlage; None, wenn ein benötigter Wert fehlt."""
    data = _Strict({k: v for k, v in values.items() if v not in (None, "")})
    if isinstance(data.get("created"), str):
        data["created"] = date.fromisoformat(data["created"])
    try:
        title = template.format_map(data)
    except (KeyError, ValueError):
        return None
    return " ".join(title.split()) or None
