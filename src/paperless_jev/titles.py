"""Titel und Beschreibungen per lokalem Sprachmodell - die einzigen generativen Schritte.

Jev entscheidet nur, formuliert aber keinen Text. Für den Titel wird darum
nach der Klassifizierung ein lokales Sprachmodell (Ollama oder OpenAI-kompatibel)
gefragt; als Stilvorlage dienen Titel bereits abgelegter Dokumente.
"""

from __future__ import annotations

import re
from typing import Any

from .llm import LLM, LLMError, complete

MAX_TITLE = 128  # Feldlänge in Paperless
MAX_TEXT = 4000

PROMPT = """Du benennst Dokumente in einem privaten Dokumentenarchiv.
Erstelle einen kurzen, aussagekräftigen deutschen Titel (höchstens 60 Zeichen) für das Dokument unten.

Regeln:
- Halte dich an den Stil der Beispieltitel aus diesem Archiv.
- Nenne, worum es konkret geht (Gegenstand, Leistung, Anlass), nicht nur die Dokumentart.
- Keine Jahreszahl, kein Datum und keine Personennamen - diese Angaben speichert das Archiv separat.
  Ausnahme: Die Beispieltitel dieses Absenders enthalten selbst Monat oder Jahr.
- Antworte nur mit dem Titel, ohne Anführungszeichen und ohne Erklärung.

Bekannte Angaben:
{facts}

Beispieltitel aus dem Archiv:
{examples}

Dokumenttext:
\"\"\"
{text}
\"\"\"

Titel:"""




def clean_title(raw: str) -> str:
    """Erste sinnvolle Zeile, ohne Denk-Blöcke, Präfixe und Anführungszeichen."""
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    for line in text.splitlines():
        line = line.strip().strip("*").strip()
        line = re.sub(r"^(titel|title)\s*:\s*", "", line, flags=re.IGNORECASE)
        line = line.strip("\"'„“”«»` ").strip()
        if line:
            return line[:MAX_TITLE].rstrip()
    return ""


def build_prompt(text: str, facts: dict[str, Any], examples: list[str]) -> str:
    fact_lines = "\n".join(f"- {k}: {v}" for k, v in facts.items() if v) or "- keine"
    example_lines = "\n".join(f"- {t}" for t in examples[:10]) or "- (keine)"
    body = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n[...]"
    return PROMPT.format(facts=fact_lines, examples=example_lines, text=body)


async def generate_title(llm: LLM, text: str, facts: dict[str, Any], examples: list[str]) -> str:
    title = clean_title(await complete(llm, build_prompt(text, facts, examples), max_tokens=60))
    if not title:
        raise LLMError("Sprachmodell lieferte keinen Titel")
    return title


DESCRIBE_PROMPT_DE = """Du formulierst kurze Kategorie-Beschreibungen für einen Dokument-Klassifizierer in einem privaten Schweizer Haushaltsarchiv.
Art der Kategorie: {kind}
Name der Kategorie: "{name}"

Stichworte des Besitzers:
\"\"\"
{notes}
\"\"\"

Titel von Dokumenten, die der Besitzer bereits in dieser Kategorie abgelegt hat:
{examples}

Schreibe EINE deutsche Beschreibung (1-2 Sätze, max. 45 Wörter) in dieser Form:
"<was dazugehört, mit allen Stichworten>." und NUR wenn die Stichworte Ausschlüsse nennen (z. B. "nicht", "kein", "ohne"), einen zweiten Satz "Nicht: <Ausschlüsse>."
Regeln:
- Jedes Stichwort muss in der Beschreibung vorkommen. Du darfst ein, zwei naheliegende Bezeichnungen ergänzen, wie sie auf solchen Dokumenten stehen.
- Die Beispieltitel dienen nur zum Verständnis der Kategorie; übernimm nie Daten, Zeiträume, Beträge oder einzelne Beispieldokumente.
- Erfinde keine Ausschlüsse und keine weiteren Kategorien.
- Eigennamen (Firmen, Personen, Orte) unverändert lassen.
- Schweizer Schreibweise: "ss" statt "ß".
Gib nur die Beschreibung aus."""

DESCRIBE_PROMPT_EN = """You write short category descriptions for a document classifier in a private Swiss household archive.
Category type: {kind}
Category name: "{name}"

The owner's keywords:
\"\"\"
{notes}
\"\"\"

Titles of documents the owner has already filed in this category:
{examples}

Write ONE English description (1-2 sentences, max. 45 words) in this form:
"<what belongs, covering every keyword>." and, ONLY if the keywords name exclusions (e.g. "not", "no", "except"), a second sentence "Not: <exclusions>."
Rules:
- Every keyword must appear in the description; translate non-English terms. You may add one or two closely related terms as they appear on such documents.
- Use the example titles only to understand the category; never copy dates, periods, amounts or single example documents.
- Do not invent exclusions or extra categories.
- Keep proper names (companies, people, places) unchanged.
Output only the description."""

DESCRIBE_PROMPTS = {"de": DESCRIBE_PROMPT_DE, "en": DESCRIBE_PROMPT_EN}

KIND_NAMES = {
    "de": {"document_type": "Dokumenttyp", "correspondent": "Korrespondent (Absender)", "tag": "Tag", "storage_path": "Speicherort"},
    "en": {"document_type": "document type", "correspondent": "correspondent (sender)", "tag": "tag", "storage_path": "storage location"},
}


async def describe_category(
    llm: LLM, kind: str, name: str, notes: str, examples: list[str], lang: str = "de"
) -> str:
    """Formuliert Stichworte zu einer Beschreibung für Jev aus, in der Sprache der Oberfläche."""
    lang = lang if lang in DESCRIBE_PROMPTS else "de"
    example_lines = "\n".join(f"- {t}" for t in examples[:8]) or ("- (keine)" if lang == "de" else "- (none)")
    prompt = DESCRIBE_PROMPTS[lang].format(
        kind=KIND_NAMES[lang].get(kind, kind), name=name, notes=notes.strip(), examples=example_lines
    )
    raw = await complete(llm, prompt, max_tokens=200)
    raw = re.sub(r"^(beschreibung|description|english|deutsch)\s*:\s*", "", raw, flags=re.IGNORECASE).strip().strip('"').strip()
    raw = strip_invented_exclusions(raw, notes)
    if not raw:
        raise LLMError("Sprachmodell lieferte keine Beschreibung")
    return raw


EXCLUSION_WORDS = re.compile(r"\b(nicht|kein|keine|ohne|ausser|außer|not|no|except)\b", re.IGNORECASE)


def strip_invented_exclusions(description: str, notes: str) -> str:
    """Entfernt "Nicht: ..."-Sätze, wenn die Stichworte keine Ausschlüsse nennen - und leere wie "Nicht: keine"."""
    parts = re.split(r"(?=\b(?:Nicht|Not):)", description)
    head, tails = parts[0].strip(), [p.strip() for p in parts[1:]]
    keep = [
        t for t in tails
        if EXCLUSION_WORDS.search(notes) and not re.fullmatch(r"(?:Nicht|Not):\s*(keine|keiner|none|n/a|-)?\.?", t, re.IGNORECASE)
    ]
    return " ".join([head, *keep]).strip()
