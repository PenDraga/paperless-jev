"""Anmeldung mit Formular und Sitzungs-Cookie.

Ersetzt den Basic-Auth-Dialog des Browsers: Passwort-Manager füllen das
Formular aus, man bleibt angemeldet (30 Tage ohne Nutzung, höchstens 90 Tage
ab Anmeldung) und kann sich abmelden. Abmelden sperrt die Sitzung auf dem
Server, auch kopierte Cookies. Ein Basic-Auth-Header wird weiter akzeptiert
(Skripte), aber nie angefordert. Ändert sich das Passwort, werden alle
Sitzungen ungültig.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from .db import Database

COOKIE = "pj_session"
MAX_AGE = 30 * 24 * 3600
ABSOLUTE_MAX_AGE = 90 * 24 * 3600  # Erneuern verlängert nie über diese Grenze ab Anmeldung
MAX_FAILURES = 5  # danach kurze Sperre pro Adresse
LOCK_SECONDS = 60


class Auth:
    def __init__(self, user: str, password: str, secret: bytes, db: Database) -> None:
        self.user, self.password, self._db = user, password, db
        self._key = hmac.new(secret, b"session\0" + password.encode(), hashlib.sha256).digest()
        self._failures: dict[str, tuple[int, float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def check(self, user: str, password: str) -> bool:
        ok_user = secrets.compare_digest(user.encode(), self.user.encode())
        ok_pw = secrets.compare_digest(password.encode(), self.password.encode())
        return ok_user and ok_pw

    def issue(self, token: str | None = None) -> str:
        """Neue Sitzung, oder Verlängerung einer gültigen (gleiche ID, gleicher Anmeldezeitpunkt)."""
        session = self._parse(token)
        start, sid = session[1:] if session else (int(time.time()), secrets.token_hex(16))
        payload = f"{min(int(time.time()) + MAX_AGE, start + ABSOLUTE_MAX_AGE)}.{start}.{sid}"
        return f"{payload}.{self._sign(payload)}"

    def _parse(self, token: str | None) -> tuple[int, int, str] | None:
        """(Ablauf, Anmeldezeitpunkt, Sitzungs-ID) eines gültigen, nicht gesperrten Tokens."""
        if not token or token.count(".") != 3:
            return None
        payload, _, sig = token.rpartition(".")
        if not secrets.compare_digest(sig, self._sign(payload)):
            return None
        exp, start, sid = payload.split(".")
        try:
            exp_i, start_i = int(exp), int(start)
        except ValueError:
            return None
        now = time.time()
        if exp_i <= now or start_i + ABSOLUTE_MAX_AGE <= now:
            return None
        if self._db.one("SELECT 1 FROM revoked_sessions WHERE sid=?", (sid,)):
            return None
        return exp_i, start_i, sid

    def valid(self, token: str | None) -> bool:
        return self._parse(token) is not None

    def needs_renewal(self, token: str | None) -> bool:
        """Nach der Hälfte der Laufzeit erneuern, solange die Höchstdauer es noch zulässt."""
        session = self._parse(token)
        if not session:
            return False
        exp, start, _ = session
        return exp < time.time() + MAX_AGE / 2 and exp < start + ABSOLUTE_MAX_AGE

    def revoke(self, token: str | None) -> None:
        """Sitzung auf dem Server sperren; gilt auch für Kopien und Verlängerungen."""
        session = self._parse(token)
        if not session:
            return
        _, start, sid = session
        self._db.execute("DELETE FROM revoked_sessions WHERE until < ?", (time.time(),))
        self._db.execute("INSERT OR IGNORE INTO revoked_sessions(sid, until) VALUES (?, ?)", (sid, start + ABSOLUTE_MAX_AGE))

    def basic_ok(self, header: str) -> bool:
        if not header.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except ValueError:
            return False
        return self.check(user, pw)

    # --- Schutz gegen Durchprobieren ---------------------------------------

    def locked(self, client: str) -> bool:
        count, since = self._failures.get(client, (0, 0.0))
        if count >= MAX_FAILURES and time.time() - since < LOCK_SECONDS:
            return True
        if count >= MAX_FAILURES:
            self._failures.pop(client, None)
        return False

    def failed(self, client: str) -> None:
        count, _ = self._failures.get(client, (0, 0.0))
        self._failures[client] = (count + 1, time.time())

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)
