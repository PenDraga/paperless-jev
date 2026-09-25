"""Verschlüsselung der gespeicherten API-Tokens.

Der Schlüssel kommt aus PJ_SECRET_KEY (beliebiger String) oder wird beim
ersten Start zufällig erzeugt und unter /data/secret.key abgelegt.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, data_dir: Path) -> None:
        secret = os.environ.get("PJ_SECRET_KEY")
        if secret:
            key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        else:
            key_file = data_dir / "secret.key"
            if not key_file.exists():
                key_file.write_bytes(Fernet.generate_key())
                key_file.chmod(0o600)
            key = key_file.read_bytes().strip()
        self._fernet = Fernet(key)

    def encrypt(self, plain: str) -> str:
        if not plain:
            return ""
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            # Schlüssel wurde gewechselt - Wert muss neu eingegeben werden.
            return ""
