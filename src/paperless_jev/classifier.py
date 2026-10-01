"""Baut die Jev-Anfrage für ein Dokument und wertet die Antwort aus.

Reine Funktionen ohne I/O, damit sie sich ohne Paperless und TypeSafe testen
lassen. Die Fragen sind bewusst englisch formuliert: Jev ist primär auf
Englisch trainiert, der Dokumenttext selbst darf deutsch bleiben.
"""

from __future__ import annotations

import re
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
        "service period, date of birth or a date mentioned in passing. If the document "
        "covers a period (e.g. an account statement or an annual certificate), choose "
        "the last day of that period."
    ),
}
TAG_INSTRUCTION = 'Does the tag "{name}" apply to this document?'
CONVENTION_HINT = (
    " Follow the owner's filing conventions: each option lists titles of documents "
    "that are already filed under it."
)
EXAMPLES_KEY = "titles_of_documents_already_filed_here"
RULE_KEY = "owner_matching_rule"
PARENT_KEY = "parent_tag"
RULE_HINT = (
    " Some options include the owner's text matching rule from Paperless: treat a match "
    "as a strong hint, but the document content decides."
)

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
    # Tags, die das Dokument schon hat und die Jev nur gegenprüft
    checked_tags: set[int] = field(default_factory=set)


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


def match_words(match: str) -> list[str]:
    """Suchbegriffe wie Paperless sie trennt: Leerzeichen, "Wortgruppen in Anführungszeichen"."""
    return [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', match)]


def rule_hint(rule: dict[str, Any]) -> str:
    """Paperless-Zuweisungsregel als englischer Satz für Jev."""
    match, algorithm = rule["match"], rule["algorithm"]
    quoted = ", ".join(f'"{w}"' for w in match_words(match))
    return {
        1: f"the text contains any of these words: {quoted}",
        2: f"the text contains all of these words: {quoted}",
        3: f'the text contains exactly: "{match}"',
        4: f"the text matches the regular expression: {match}",
        5: f'the text approximately contains: "{match}"',
    }[algorithm]


def _criterion(
    kind: str, oid: int, descriptions: dict, examples: Examples, rules: dict | None = None
) -> Any:
    """Beschreibung, Paperless-Regel und/oder Beispieltitel als (strukturiertes) Kriterium."""
    text = descriptions.get((kind, oid), {}).get("text", "")
    titles = examples.get((kind, oid))
    rule = (rules or {}).get((kind, oid))
    if not titles and not rule:
        return text or None
    criterion: dict[str, Any] = {"what": text} if text else {}
    if rule:
        criterion[RULE_KEY] = rule_hint(rule)
    if titles:
        criterion[EXAMPLES_KEY] = titles
    return criterion


def _parent_context(tid: int, meta: Metadata, descriptions: dict) -> str | None:
    """Beschreibung des nächsten beschriebenen Obertags als Kontext für einen Untertag."""
    for parent in meta.tag_ancestors(tid):
        text = descriptions.get(("tag", parent), {}).get("text", "")
        if text:
            return f"{meta.tag_label(parent)}: {text}"
    return None


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
    rules = meta.rules if cfg.get("paperless_rules") else {}

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
            label: _criterion(kind, oid, descriptions, examples, rules) for label, oid in labels.items()
        }
        criteria[NONE] = NONE_CRITERION
        instructions = INSTRUCTIONS[name]
        if any(isinstance(c, dict) and EXAMPLES_KEY in c for c in criteria.values()):
            instructions += CONVENTION_HINT
        if any(isinstance(c, dict) and RULE_KEY in c for c in criteria.values()):
            instructions += RULE_HINT
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
        hidden = excluded_tags(meta, cfg)
        present = set(doc.get("tags", []))
        active = {tid: n for tid, n in _active("tag", meta.tags, descriptions).items() if tid not in hidden}
        # Obertags vorhandener Untertags sind dadurch begründet - nicht einzeln gegenprüfen
        implied = {a for tid in present for a in meta.tag_ancestors(tid)}
        # Vorhandene Tags zuerst (Gegenprüfung), danach mögliche neue
        if cfg.get("verify_tags", True):
            req.checked_tags = {tid for tid in active if tid in present and tid not in implied}
        tags = {tid: n for tid, n in active.items() if tid in req.checked_tags}
        tags |= {tid: n for tid, n in active.items() if tid not in present}
        for tid in list(tags)[:MAX_TAG_QUESTIONS]:
            # Untertags mit Pfad, damit z. B. "Unterhalt" unter "Haus" und "Auto" eindeutig ist
            tag_name = meta.tag_label(tid)
            question: dict[str, Any] = {
                "type": "noul",
                "instructions": TAG_INSTRUCTION.format(name=tag_name),
            }
            desc = _criterion("tag", tid, descriptions, examples, rules)
            if parent := _parent_context(tid, meta, descriptions):
                desc = {"what": desc, PARENT_KEY: parent} if isinstance(desc, str) else {**(desc or {}), PARENT_KEY: parent}
            if desc:
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
        "tag_checks": [],
    }
    for name in SINGLE_FIELDS:
        answer = answers.get(name)
        if not answer:
            continue
        label = answer.get("choice")
        value = req.options[name].get(label)
        confidence = float(answer.get("confidence", 0.0))
        fallback = None
        if value is None and name == "correspondent" and cfg.get("correspondent_fallback"):
            # "keiner davon" heisst hier: Sammel-Korrespondent (z. B. "Diverses")
            fallback = {v.lower(): k for k, v in meta.correspondents.items()}.get(
                cfg["correspondent_fallback"].strip().lower()
            )
        if fallback is not None:
            value, label = fallback, meta.correspondents[fallback]
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
        if fallback is not None:
            # Jev hat "keiner passt" gesagt - der Wert ist nur der Sammel-Korrespondent
            result["fields"][name]["fallback"] = True
    tag_spec = cfg["fields"]["tags"]
    for key, answer in answers.items():
        if not key.startswith("tag:"):
            continue
        tid = int(key[4:])
        p = float(answer.get("noul", 0.0))
        label = meta.tag_label(tid)
        if tid in req.checked_tags:
            result["tag_checks"].append({"id": tid, "label": label, "p": round(p, 4), "verdict": tag_verdict(p, tag_spec)})
            continue
        result["tags"].append({"id": tid, "label": label, "p": round(p, 4), "level": _level(p, tag_spec)})
    result["tags"].sort(key=lambda t: t["p"], reverse=True)
    result["tag_checks"].sort(key=lambda t: t["p"])
    return result


def tag_verdict(p: float, spec: dict[str, Any]) -> str:
    """Gegenprüfung eines vorhandenen Tags: so sicher "nein" wie sonst "ja" -> Widerspruch."""
    if p <= 1 - float(spec["auto"]):
        return "conflict"
    return "ok" if p >= 0.5 else "unsure"


def plan(result: dict[str, Any], doc: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Welche Werte sicher genug zum Setzen sind und ob ein Mensch draufschauen muss."""
    updates: dict[str, Any] = {}
    needs_review = False
    for name, spec in cfg["fields"].items():
        if name == "tags" or not spec["enabled"]:
            continue
        found = result["fields"].get(name)
        current = doc.get(name)
        if found and found.get("fallback"):
            # Sammel-Korrespondent: nur für leere Felder, nie als Widerspruch zu einem vorhandenen Wert
            if not current:
                if found["level"] == "auto":
                    updates[name] = found["value"]
                needs_review = needs_review or cfg.get("fallback_review", True) or found["level"] != "auto"
            elif current == found["value"] and cfg.get("fallback_review", True):
                # Paperless hat selbst schon den Sammel-Korrespondenten gesetzt (z. B. gelernte Zuordnung)
                # und Jev findet keinen passenden: trotzdem prüfen lassen
                needs_review = True
            continue
        if name == "created":
            # Paperless setzt immer ein Datum; nur korrigieren, wenn Jev sicher ist.
            if found and found["value"] and found["value"] != current:
                if found["level"] == "auto":
                    updates[name] = found["value"]
                elif found["level"] == "suggest":
                    needs_review = True
            continue
        if current and not cfg["overwrite"]:
            conflict = (
                found is not None
                and found["level"] == "auto"
                and found["value"] is not None
                and found["value"] != current
            )
            # Sehr sicherer Widerspruch: Jev korrigiert den vorhandenen Wert selbst (0 = nie)
            overwrite_above = float(cfg.get("overwrite_above") or 0)
            if conflict and overwrite_above > 0 and found["confidence"] >= overwrite_above:
                updates[name] = found["value"]
            # Sonst bleibt der vorhandene Wert - bei sicherem Widerspruch entscheidet ein Mensch
            # (z. B. zu breite Paperless-Zuordnungsregeln).
            elif conflict and cfg.get("review_conflicts", True):
                needs_review = True
            continue
        if found and found["level"] == "auto" and found["value"] != current:
            updates[name] = found["value"]
        elif not current:
            needs_review = True
    if cfg["fields"]["tags"]["enabled"]:
        updates["tags_add"] = [t["id"] for t in result["tags"] if t["level"] == "auto"]
        if cfg.get("tags_force_review") and any(t["level"] == "suggest" for t in result["tags"]):
            needs_review = True
        # Ein vorhandener Tag passt laut Jev sicher nicht: ein Mensch entscheidet, entfernt wird nichts
        if cfg.get("review_conflicts", True) and any(t["verdict"] == "conflict" for t in result.get("tag_checks", [])):
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
