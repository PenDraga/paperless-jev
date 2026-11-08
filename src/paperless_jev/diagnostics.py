"""Fehlermeldungen beim Anzeigen übersetzen, auch bereits gespeicherte.

Jobs speichern die deutsche Meldung. Erkannt werden nur eigene Formulierungen;
Antworten von Paperless oder vom Modell und HTTP-Codes bleiben wörtlich.
"""

from __future__ import annotations

import re
from string import Formatter

from .i18n import gettext

TEMPLATES = (
    "Sprachmodell lieferte keinen brauchbaren Titel: {detail}",
    "Sprachmodell nicht erreichbar ({url}): {detail}",
    "Sprachmodell HTTP {status}: {detail}",
    "Paperless-Verarbeitung fehlgeschlagen: {detail}",
    "{service} nicht erreichbar: {detail}",
    "{service}: keine Antwort",
)
MESSAGES = (
    "Sprachmodell lieferte keinen brauchbaren Titel",
    "Instanz nicht gefunden",
    "Job oder Instanz nicht gefunden",
    "Cloudflare: unvollständige Antwort",
    "Cloudflare: ungültige Tag-Wahrscheinlichkeit",
)
_PATTERNS = [
    (t, re.compile("".join(re.escape(lit) + (f"(?P<{f}>.*?)" if f else "")
                           for lit, f, _, _ in Formatter().parse(t)), re.DOTALL))
    for t in TEMPLATES
]


def diagnostic(value: str | Exception) -> str:
    """Meldung in der Sprache der Oberfläche; bei Sprachmodell-Fehlern die öffentliche Kurzform."""
    text = getattr(value, "public_message", None) or str(value)
    for template, pattern in _PATTERNS:
        if m := pattern.fullmatch(text):
            return gettext(template, **m.groupdict())
    return gettext(text)
