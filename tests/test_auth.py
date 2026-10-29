"""Anmeldung per Formular und Sitzungs-Cookie statt Basic-Auth-Dialog."""

import base64

import pytest
from fastapi.testclient import TestClient

from paperless_jev import app as app_module
from paperless_jev.auth import COOKIE, Auth


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "ADMIN_PASSWORD", "geheim")
    with TestClient(app_module.app, headers={"Accept-Language": "de-CH"}) as c:
        yield c


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


def test_session_tokens():
    auth = Auth("admin", "pw", b"k" * 32)
    token = auth.issue()
    assert auth.valid(token)
    assert not auth.valid(token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert not Auth("admin", "neues-pw", b"k" * 32).valid(token)  # Passwort geändert: Sitzungen ungültig
    assert not Auth("admin", "pw", b"x" * 32).valid(token)
    assert not auth.valid(None) and not auth.valid("1.2") and not auth.valid("abc.def.ghi")


def test_session_is_renewed_while_in_use(client, monkeypatch):
    import time as time_mod

    login(client)
    assert "set-cookie" not in client.get("/").headers  # frisch: nicht erneuern
    real = time_mod.time
    monkeypatch.setattr("paperless_jev.auth.time.time", lambda: real() + 20 * 24 * 3600)
    r = client.get("/")
    assert r.status_code == 200 and COOKIE in r.headers["set-cookie"]


def test_without_password_everything_is_open(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "auth", Auth("admin", "", b"k" * 32))
    assert client.get("/").status_code == 200
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    assert 'action="/logout"' not in client.get("/").text
