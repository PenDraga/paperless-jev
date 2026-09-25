"""Kandidaten im Code finden, damit Jev nur noch auswählen muss.

Jev rechnet nicht und vergleicht keine Daten zuverlässig (siehe
docs.typesafe.ai/model-jaggedness). Darum sucht hier Code alle
Datumsangaben bzw. passende Korrespondenten, und Jev wählt den richtigen aus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

MONTHS = {
    "januar": 1, "jänner": 1, "january": 1, "jan": 1,
    "februar": 2, "february": 2, "feb": 2,
    "märz": 3, "maerz": 3, "march": 3, "mrz": 3, "mär": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juni": 6, "june": 6, "jun": 6,
    "juli": 7, "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11,
    "dezember": 12, "december": 12, "dez": 12, "dec": 12,
}  # fmt: skip
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

NUMERIC_DMY = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?![\d])")
ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
NAMED_DMY = re.compile(
    rf"(?<!\d)(\d{{1,2}})\.?\s*({_MONTH_RE})\.?\s+(\d{{4}})(?!\d)", re.IGNORECASE
)
NAMED_MDY = re.compile(
    rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)", re.IGNORECASE
)

MAX_DATE_CANDIDATES = 40
MIN_YEAR = 1990


@dataclass(frozen=True)
class DateCandidate:
    label: str
    value: date
    position: int
    context: str


def _year(raw: str) -> int:
    year = int(raw)
    if year < 100:
        pivot = date.today().year % 100 + 1
        year += 2000 if year <= pivot else 1900
    return year


def _make(y: int, m: int, d: int) -> date | None:
    try:
        value = date(y, m, d)
    except ValueError:
        return None
    if not MIN_YEAR <= value.year <= date.today().year + 1:
        return None
    return value


def find_dates(text: str, limit: int = MAX_DATE_CANDIDATES) -> list[DateCandidate]:
    """Alle plausiblen Datumsangaben in Dokumentreihenfolge, je Datum nur einmal."""
    found: list[tuple[int, str, date]] = []
    for m in NUMERIC_DMY.finditer(text):
        if value := _make(_year(m[3]), int(m[2]), int(m[1])):
            found.append((m.start(), m[0], value))
    for m in ISO.finditer(text):
        if value := _make(int(m[1]), int(m[2]), int(m[3])):
            found.append((m.start(), m[0], value))
    for m in NAMED_DMY.finditer(text):
        if value := _make(int(m[3]), MONTHS[m[2].lower()], int(m[1])):
            found.append((m.start(), m[0], value))
    for m in NAMED_MDY.finditer(text):
        if value := _make(int(m[3]), MONTHS[m[1].lower()], int(m[2])):
            found.append((m.start(), m[0], value))

    found.sort(key=lambda f: f[0])
    seen: set[date] = set()
    labels: set[str] = set()
    result: list[DateCandidate] = []
    for pos, raw, value in found:
        label = " ".join(raw.split())
        if value in seen or label in labels:
            continue
        seen.add(value)
        labels.add(label)
        start, end = max(0, pos - 50), min(len(text), pos + len(raw) + 30)
        context = " ".join(text[start:end].split())
        result.append(DateCandidate(label, value, pos, context))
        if len(result) >= limit:
            break
    return result


def correspondent_candidates(
    text: str,
    correspondents: dict[int, str],
    preferred: set[int] | None = None,
    limit: int = 30,
    threshold: int = 75,
) -> dict[int, str]:
    """Vorauswahl bei vielen Korrespondenten.

    Bis ``limit`` Korrespondenten gehen alle an Jev. Darüber zählen nur die,
    deren Name unscharf im Text vorkommt, plus die aus ähnlichen Dokumenten.
    """
    if len(correspondents) <= limit:
        return dict(correspondents)
    haystack = text.lower()
    scored = []
    for cid, name in correspondents.items():
        needle = name.lower().strip()
        if len(needle) < 3:
            continue
        score = 100 if needle in haystack else fuzz.partial_ratio(needle, haystack)
        if score >= threshold:
            scored.append((score, cid))
    scored.sort(reverse=True)
    chosen = [cid for _, cid in scored[:limit]]
    for cid in preferred or ():
        if cid in correspondents and cid not in chosen:
            chosen.append(cid)
    return {cid: correspondents[cid] for cid in chosen}
