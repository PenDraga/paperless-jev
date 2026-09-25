"""Titel per lokalem Ollama - der einzige generative Schritt.

Jev entscheidet nur, formuliert aber keinen Text. Für den Titel wird darum
nach der Klassifizierung ein kleines lokales Sprachmodell gefragt; als
Stilvorlage dienen Titel bereits abgelegter Dokumente.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

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


class TitleError(Exception):
    pass


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


async def generate_title(
    url: str,
    model: str,
    text: str,
    facts: dict[str, Any],
    examples: list[str],
    timeout: float = 180.0,
) -> str:
    payload = {
        "model": model,
        "prompt": build_prompt(text, facts, examples),
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 60},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(f"{url.rstrip('/')}/api/generate", json=payload)
    except httpx.HTTPError as e:
        raise TitleError(f"Ollama nicht erreichbar: {e}") from e
    if resp.status_code >= 400:
        raise TitleError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
    title = clean_title(resp.json().get("response", ""))
    if not title:
        raise TitleError("Ollama lieferte keinen Titel")
    return title


async def list_models(url: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(f"{url.rstrip('/')}/api/tags")
    except httpx.HTTPError as e:
        raise TitleError(f"Ollama nicht erreichbar: {e}") from e
    if resp.status_code >= 400:
        raise TitleError(f"Ollama HTTP {resp.status_code}")
    return [m["name"] for m in resp.json().get("models", [])]
