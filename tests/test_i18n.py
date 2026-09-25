"""Jeder übersetzbare Text der Oberfläche hat eine englische Fassung mit denselben Platzhaltern."""

import re
import string
from pathlib import Path

from paperless_jev import i18n
from paperless_jev.app import MODE_LABELS, STATUS_LABELS
from paperless_jev.config import FIELD_LABELS
from paperless_jev.llm import PROVIDERS

SRC = Path(__file__).parent.parent / "src" / "paperless_jev"
CALL = re.compile(r"""\b_\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")


def _strings() -> set[str]:
    found: set[str] = set()
    for path in [*SRC.glob("templates/*.html"), SRC / "app.py"]:
        for m in CALL.finditer(path.read_text(encoding="utf-8")):
            raw = m.group(1) if m.group(1) is not None else m.group(2)
            found.add(raw.encode().decode("unicode_escape").encode("latin-1").decode("utf-8"))
    found |= {*STATUS_LABELS.values(), *MODE_LABELS.values(), *FIELD_LABELS.values(), *PROVIDERS.values()}
    found |= {"Dokumenttypen", "Korrespondenten", "Tags", "Speicherpfade"}
    return found


def _fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_every_ui_string_has_an_english_translation():
    missing = sorted(s for s in _strings() if s not in i18n.EN)
    assert not missing, "Fehlende Übersetzungen:\n" + "\n".join(missing)


def test_placeholders_match():
    for de, en in i18n.EN.items():
        assert _fields(de) == _fields(en), de


def test_pick_language():
    assert i18n.pick("en", "de-CH") == "en"
    assert i18n.pick(None, "en-US,en;q=0.9") == "en"
    assert i18n.pick(None, "fr-CH,fr;q=0.9") == "de"
    assert i18n.pick("xx", None) == "de"
