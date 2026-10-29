"""Anmeldung mit Formular und Sitzungs-Cookie.

Ersetzt den Basic-Auth-Dialog des Browsers: Passwort-Manager füllen das
Formular aus, man bleibt angemeldet (30 Tage) und kann sich abmelden. Ein
Basic-Auth-Header wird weiter akzeptiert (Skripte), aber nie angefordert.
Ändert sich das Passwort, werden alle Sitzungen ungültig.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

COOKIE = "pj_session"
MAX_AGE = 30 * 24 * 3600
MAX_FAILURES = 5  # danach kurze Sperre pro Adresse
LOCK_SECONDS = 60


class Auth:
    def __init__(self, user: str, password: str, secret: bytes) -> None:
        self.user, self.password = user, password
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

    def issue(self) -> str:
        payload = f"{int(time.time()) + MAX_AGE}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def expires(self, token: str | None) -> float:
        """Ablaufzeit eines gültigen Tokens, sonst 0."""
        if not token or token.count(".") != 2:
            return 0.0
        payload, _, sig = token.rpartition(".")
        if not secrets.compare_digest(sig, self._sign(payload)):
            return 0.0
        try:
            exp = float(payload.split(".")[0])
        except ValueError:
            return 0.0
        return exp if exp > time.time() else 0.0

    def valid(self, token: str | None) -> bool:
        return self.expires(token) > 0

    def needs_renewal(self, token: str | None) -> bool:
        """Nach der Hälfte der Laufzeit erneuern: abgemeldet wird erst nach 30 Tagen ohne Nutzung."""
        exp = self.expires(token)
        return 0 < exp < time.time() + MAX_AGE / 2

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
