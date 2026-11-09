"""Jeder übersetzbare Text der Oberfläche hat in jeder Sprache eine Fassung mit denselben Platzhaltern und HTML-Tags."""

import re
import string
from pathlib import Path

import httpx
import pytest

from paperless_jev import diagnostics, i18n
from paperless_jev.app import MODE_LABELS, STATUS_LABELS
from paperless_jev.config import FIELD_LABELS
from paperless_jev.llm import PROVIDERS

from .test_llm import _patch
from .test_processor import client  # noqa: F401

SRC = Path(__file__).parent.parent / "src" / "paperless_jev"
CALL = re.compile(r"""\b(?:_|log_text)\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")


def _strings() -> set[str]:
    found: set[str] = set()
    for path in [*SRC.glob("templates/*.html"), SRC / "app.py", SRC / "processor.py"]:
        for m in CALL.finditer(path.read_text(encoding="utf-8")):
            raw = m.group(1) if m.group(1) is not None else m.group(2)
            found.add(raw.encode().decode("unicode_escape").encode("latin-1").decode("utf-8"))
    found |= {*STATUS_LABELS.values(), *MODE_LABELS.values(), *FIELD_LABELS.values(), *PROVIDERS.values()}
    found |= {"Dokumenttypen", "Korrespondenten", "Tags", "Speicherpfade"}
    found |= {*diagnostics.TEMPLATES, *diagnostics.MESSAGES}
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


# Fehlermeldungen in der Sprache der Oberfläche (nach #2)

@pytest.mark.parametrize("lang", ["en", "de", "fr", "pl"])
def test_title_failure_uses_selected_language(client, monkeypatch, lang):  # noqa: F811
    expected = i18n.TRANSLATIONS.get(lang, {}).get("Sprachmodell lieferte keinen brauchbaren Titel",
                                                   "Sprachmodell lieferte keinen brauchbaren Titel")
    client.cookies.set("lang", lang)
    client.app.state.config.update({"title_mode": "llm", "llm_url": "http://model.example", "llm_model": "m"})
    job_id = client.app.state.db.create_job(1, 42, "test")
    _patch(monkeypatch, lambda request: httpx.Response(200, json={"response": ""}))
    for suffix in ("?format=json", ""):
        r = client.post(f"/jobs/{job_id}/title{suffix}")
        message = r.json()["error"] if suffix else r.text
        assert expected in message and "''" not in message


def test_saved_errors_follow_language_when_reopened(client):  # noqa: F811
    db = client.app.state.db
    job_id = db.create_job(1, 42, "test")
    db.update_job(job_id, status="error", error="Ollama nicht erreichbar: connection refused")
    for lang, expected in (("en", "Ollama unreachable: connection refused"),
                           ("de", "Ollama nicht erreichbar: connection refused")):
        client.cookies.set("lang", lang)
        for path in ("/log", f"/jobs/{job_id}"):
            assert expected in client.get(path).text
    assert db.job(job_id)["error"] == "Ollama nicht erreichbar: connection refused"


def test_unknown_errors_stay_verbatim():
    token = i18n.current.set("en")
    try:
        assert diagnostics.diagnostic("Paperless GET /api/x: HTTP 500 boom") == "Paperless GET /api/x: HTTP 500 boom"
        assert diagnostics.diagnostic("Instanz nicht gefunden") == "Instance not found"
    finally:
        i18n.current.reset(token)


def test_connection_test_failure_is_translated_without_provider_body(client, monkeypatch):  # noqa: F811
    client.cookies.set("lang", "en")
    _patch(monkeypatch, lambda request: httpx.Response(401, text="private-provider-detail"))
    r = client.post("/setup/llm/test", data={"llm_url": "http://model.example", "llm_model": "m"})
    assert "HTTP 401" in r.text and "Sprachmodell" not in r.text and "private-provider-detail" not in r.text


def test_cross_origin_rejection_uses_cookie_language(client):  # noqa: F811
    client.cookies.set("lang", "fr")
    r = client.post("/run", headers={"Origin": "https://other.example"})
    assert r.status_code == 403 and r.text.strip() == i18n.TRANSLATIONS["fr"]["Anfrage einer fremden Seite abgewiesen"]


def test_logs_and_webhook_use_central_language_not_visitor_language(client, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(i18n, "LOG_LANGUAGE", "en")
    client.cookies.set("lang", "fr")
    assert client.post("/hook/home?token=wrong").json()["detail"] == "Invalid token"
    client.post("/run", headers={"Origin": "https://other.example"})
    assert "Cross-origin request rejected: POST /run (Origin https://other.example)" in caplog.text
    monkeypatch.setattr(i18n, "LOG_LANGUAGE", "de")
    assert client.post("/hook/home?token=wrong").json()["detail"] == "Ungültiges Token"


def test_log_detail_translates_own_wording_and_keeps_provider_text(monkeypatch):
    monkeypatch.setattr(i18n, "LOG_LANGUAGE", "en")
    assert diagnostics.log_detail("Paperless nicht erreichbar: connection refused") == "Paperless unreachable: connection refused"
    assert diagnostics.log_detail("Paperless GET /api/x: HTTP 500 boom") == "Paperless GET /api/x: HTTP 500 boom"
