"""Jeder übersetzbare Text der Oberfläche hat in jeder Sprache eine Fassung mit denselben Platzhaltern und HTML-Tags."""

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


def _tags(text: str) -> list[str]:
    return sorted(re.findall(r"</?[a-z]+[^>]*>", text))


def test_placeholders_match():
    for de, en in i18n.EN.items():
        assert _fields(de) == _fields(en), de


def test_every_language_is_complete():
    for code in i18n.LANGUAGES:
        if code in ("de", "en"):
            continue
        tr = i18n.TRANSLATIONS.get(code)
        assert tr is not None, f"locales/{code}.json fehlt"
        assert set(tr) == set(i18n.EN), f"{code}: fehlend {sorted(set(i18n.EN) - set(tr))[:5]}, überzählig {sorted(set(tr) - set(i18n.EN))[:5]}"
        for de, text in tr.items():
            assert text.strip(), (code, de)
            assert _fields(text) == _fields(de), (code, de)
            assert _tags(text) == _tags(i18n.EN[de]), (code, de)


def test_other_languages_translate_and_fall_back_to_english():
    token = i18n.current.set("fr")
    try:
        assert i18n.gettext("Übersicht") == i18n.TRANSLATIONS["fr"]["Übersicht"]
        assert i18n.gettext("gibt es nicht") == "gibt es nicht"
    finally:
        i18n.current.reset(token)


def test_pick_language():
    assert i18n.pick("en", "de-CH") == "en"
    assert i18n.pick(None, "en-US,en;q=0.9") == "en"
    assert i18n.pick(None, "fr-CH,fr;q=0.9") == "fr"
    assert i18n.pick(None, "nl-NL,nl;q=0.9,en;q=0.8") == "nl"
    assert i18n.pick(None, "pt-BR,pt;q=0.9") == "en"
    assert i18n.pick(None, "de-CH,de;q=0.9,en;q=0.8") == "de"
    assert i18n.pick("xx", None) == "en"
