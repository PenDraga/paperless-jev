"""Anmeldung per Formular und Sitzungs-Cookie statt Basic-Auth-Dialog."""

import base64
import time

import pytest
from fastapi.testclient import TestClient

from paperless_jev import app as app_module
from paperless_jev.auth import ABSOLUTE_MAX_AGE, COOKIE, Auth
from paperless_jev.db import Database

REAL_TIME = time.time


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "geheim")
    return lambda **kw: TestClient(app_module.app, headers={"Accept-Language": "de-CH"}, **kw)


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "auth.db")


def later(monkeypatch, seconds):
    monkeypatch.setattr("paperless_jev.auth.time.time", lambda: REAL_TIME() + seconds)


def login(client, password="geheim", next="/"):
    return client.post("/login", data={"username": "admin", "password": password, "next": next}, follow_redirects=False)


def test_pages_redirect_to_login_without_browser_dialog(client):
    r = client.get("/log?status=open", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Flog%3Fstatus%3Dopen"
    assert "www-authenticate" not in r.headers
    page = client.get(r.headers["location"]).text
    assert 'autocomplete="current-password"' in page and 'autocomplete="username"' in page
    assert 'class="tabs"' not in page  # keine Navigation vor der Anmeldung


def test_htmx_and_api_requests_get_401_without_challenge(client):
    r = client.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["hx-redirect"] == "/login" and "www-authenticate" not in r.headers
    assert client.post("/run", follow_redirects=False).status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_login_keeps_session_and_logout_ends_it(client):
    assert "falsch" in client.get(login(client, "nein").headers["location"]).text
    r = login(client, next="/rules")
    assert r.status_code == 303 and r.headers["location"] == "/rules"
    cookie = r.headers["set-cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "Max-Age=2592000" in cookie and "SameSite=lax" in cookie
    page = client.get("/rules").text
    assert 'action="/logout"' in page and 'class="tabs"' in page
    r = client.post("/logout", follow_redirects=False)
    assert r.headers["location"].startswith("/login")
    assert client.get("/rules", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 303


def test_no_open_redirect_after_login(client):
    assert login(client, next="https://evil.example/").headers["location"] == "/"
    assert login(client, next="//evil.example").headers["location"] == "/"


def test_basic_auth_header_still_works_for_scripts(client):
    good = base64.b64encode(b"admin:geheim").decode()
    assert client.get("/", headers={"Authorization": f"Basic {good}"}).status_code == 200


def test_lockout_after_repeated_failures(client):
    for _ in range(5):
        login(client, "falsch")
    r = login(client)  # richtiges Passwort, aber gesperrt
    assert "set-cookie" not in r.headers and "Fehlversuche" in client.get(r.headers["location"]).text


def test_session_tokens(db):
    auth = Auth("admin", "pw", b"k" * 32, db)
    token = auth.issue()
    assert auth.valid(token)
    assert not auth.valid(token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert not Auth("admin", "neues-pw", b"k" * 32, db).valid(token)  # Passwort geändert: Sitzungen ungültig
    assert not Auth("admin", "pw", b"x" * 32, db).valid(token)
    assert not auth.valid(None) and not auth.valid("1.2") and not auth.valid("abc.def.ghi.jkl")


def test_session_is_renewed_while_in_use(client, monkeypatch):
    login(client)
    assert "set-cookie" not in client.get("/").headers  # frisch: nicht erneuern
    later(monkeypatch, 20 * 24 * 3600)
    r = client.get("/")
    assert r.status_code == 200 and COOKIE in r.headers["set-cookie"]


def test_logout_ends_copied_and_renewed_cookies(client, monkeypatch):
    login(client)
    copied = client.cookies[COOKIE]
    later(monkeypatch, 20 * 24 * 3600)
    client.get("/")  # Verlängerung: neues Cookie, gleiche Sitzung
    renewed = client.cookies[COOKIE]
    assert renewed != copied
    client.post("/logout")
    for token in (copied, renewed):
        client.cookies.set(COOKIE, token)
        assert client.get("/rules", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 303


def test_logout_survives_restart(make_client):
    with make_client() as c:
        login(c)
        copied = c.cookies[COOKIE]
        c.post("/logout")
    with make_client(cookies={COOKIE: copied}) as c:
        assert c.get("/", follow_redirects=False).status_code == 401


def test_renewal_stops_after_90_days(db, monkeypatch):
    auth = Auth("admin", "pw", b"k" * 32, db)
    token = auth.issue()
    for day in range(20, 90, 20):  # in Gebrauch: immer wieder verlängert
        later(monkeypatch, day * 24 * 3600)
        assert auth.valid(token)
        token = auth.issue(token) if auth.needs_renewal(token) else token
    later(monkeypatch, ABSOLUTE_MAX_AGE + 1)
    assert not auth.valid(token)


def test_without_password_the_ui_is_locked(client, monkeypatch, db):
    monkeypatch.setattr(client.app.state, "auth", Auth("admin", "", b"k" * 32, db))
    r = client.get("/")
    assert r.status_code == 503 and "PJ_ALLOW_NO_LOGIN" in r.text
    assert client.post("/run").status_code == 503
    assert client.get("/health").status_code == 200
    assert client.post("/hook/nope?token=x").status_code != 503  # Webhooks prüfen ihr Token selbst


def test_without_password_open_only_when_allowed(client, monkeypatch, db):
    monkeypatch.setattr(client.app.state, "auth", Auth("admin", "", b"k" * 32, db))
    monkeypatch.setattr(app_module, "ALLOW_NO_LOGIN", True)
    assert client.get("/").status_code == 200
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    assert 'action="/logout"' not in client.get("/").text


def login_from(client, ip, password, extra=None):
    headers = {"X-Forwarded-For": ip, **(extra or {})}
    return client.post("/login", data={"username": "admin", "password": password}, headers=headers, follow_redirects=False)


def test_forwarded_headers_from_untrusted_sender_are_ignored(client):
    spoof = {"X-Forwarded-Proto": "https"}
    for i in range(5):
        login_from(client, f"198.51.100.{i}", "falsch", spoof)
    assert "set-cookie" not in login_from(client, "198.51.100.99", "geheim", spoof).headers  # neue Adresse hilft nicht
    client.app.state.auth.succeeded("testclient")
    r = login_from(client, "198.51.100.99", "geheim", spoof)
    assert COOKIE in r.headers["set-cookie"] and "Secure" not in r.headers["set-cookie"]


def test_forwarded_headers_from_trusted_proxy_are_used(make_client):
    with make_client(client=("127.0.0.1", 40000)) as c:
        for _ in range(5):
            login_from(c, "198.51.100.1", "falsch")
        assert "set-cookie" not in login_from(c, "198.51.100.1", "geheim").headers
        r = login_from(c, "198.51.100.2", "geheim", {"X-Forwarded-Proto": "https"})
        assert "Secure" in r.headers["set-cookie"]


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "same-site", "Origin": "https://other.testserver"},
    {"Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"},  # Fetch Metadata hat Vorrang
])
def test_changes_from_other_sites_are_rejected(client, headers):
    login(client)
    assert client.post("/logout", headers=headers, follow_redirects=False).status_code == 403
    assert client.post("/login", data={"username": "admin", "password": "geheim"}, headers=headers).status_code == 403
    assert client.get("/", headers=headers).status_code == 200  # Lesen bleibt erlaubt


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}, {"Origin": "http://testserver"}, {}])
def test_same_origin_and_scripts_pass(client, headers):
    login(client)
    r = client.post("/logout", headers=headers, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_webhook_skips_origin_check(client):
    r = client.post("/hook/nope?token=x", headers={"Origin": "https://evil.example"})
    assert "Cross-origin" not in r.text
