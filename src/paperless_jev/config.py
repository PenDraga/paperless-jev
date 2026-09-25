"""Einstellungen und Paperless-Instanzen, gepflegt über die Web-UI."""

from __future__ import annotations

import copy
import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .db import Database
from .vault import Vault

# Felder, die Jev per Choice bestimmt. Schwellwerte beziehen sich auf die
# Confidence der Antwort (0..1).
SINGLE_FIELDS = ("document_type", "correspondent", "storage_path", "created")

FIELD_LABELS = {
    "document_type": "Dokumenttyp",
    "correspondent": "Korrespondent",
    "storage_path": "Speicherpfad",
    "created": "Ausstellungsdatum",
    "tags": "Tags",
}

DEFAULTS: dict[str, Any] = {
    "typesafe_api_key": "",
    "model": "jev-latest",
    # dry_run: nur protokollieren | review: alles in die Review-Queue |
    # auto: sichere Felder direkt setzen, unsichere in die Review-Queue
    "mode": "dry_run",
    "poll_minutes": 5,
    "max_chars": 12000,
    "similar_docs": 3,
    # Titel bereits abgelegter Dokumente je Dokumenttyp/Tag/Speicherpfad als
    # Beispiele in den Kriterien - so lernt Jev deine Ablage-Konventionen.
    "examples": 5,
    # Unsichere Tag-Vorschläge allein schicken ein Dokument nicht ins Review.
    "tags_force_review": False,
    # Korrespondent, der gesetzt wird, wenn keiner der bestehenden passt
    # (Sammel-Korrespondent wie "Diverses"); leer = Feld bleibt leer.
    "correspondent_fallback": "",
    "overwrite": False,
    "remove_inbox": True,
    "tag_done": "ai-klassifiziert",
    "tag_review": "ai-review",
    "tag_ignore": "ai-ignorieren",
    # off: Titel bleibt | template: aus Vorlage | ollama: lokal generiert
    "title_mode": "off",
    "title_template": "{document_type} {correspondent} {created:%Y-%m}",
    "ollama_url": "",
    "ollama_model": "qwen3:8b",
    "fields": {
        "document_type": {"enabled": True, "auto": 0.85, "review": 0.4},
        "correspondent": {"enabled": True, "auto": 0.85, "review": 0.4},
        "storage_path": {"enabled": False, "auto": 0.85, "review": 0.4},
        "created": {"enabled": True, "auto": 0.9, "review": 0.5},
        "tags": {"enabled": True, "auto": 0.9, "review": 0.7},
    },
    "webhook_secret": "",
}

SECRET_KEYS = ("typesafe_api_key",)


@dataclass
class Instance:
    id: int
    name: str
    url: str
    public_url: str
    token: str
    enabled: bool

    @property
    def browse_url(self) -> str:
        return (self.public_url or self.url).rstrip("/")

    @property
    def host_header(self) -> str | None:
        """Öffentlicher Hostname für den Host-Header, falls abweichend von der API-URL."""
        public = urlparse(self.public_url).netloc
        return public if public and public != urlparse(self.url).netloc else None


class Config:
    def __init__(self, db: Database, vault: Vault) -> None:
        self.db = db
        self.vault = vault
        if not self.all()["webhook_secret"]:
            self.update({"webhook_secret": secrets.token_urlsafe(24)})

    # --- globale Einstellungen ------------------------------------------

    def all(self) -> dict[str, Any]:
        cfg = copy.deepcopy(DEFAULTS)
        rows = self.db.query("SELECT key, value FROM settings")
        stored = {r["key"] for r in rows}
        for row in rows:
            value = json.loads(row["value"])
            if row["key"] in SECRET_KEYS:
                value = self.vault.decrypt(value)
            if row["key"] == "fields":
                for name, spec in value.items():
                    cfg["fields"].setdefault(name, {}).update(spec)
            else:
                cfg[row["key"]] = value
        # Einstellung aus v0.1: title_enabled -> title_mode
        if "title_mode" not in stored and cfg.pop("title_enabled", False):
            cfg["title_mode"] = "template"
        cfg.pop("title_enabled", None)
        return cfg

    def update(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            if key in SECRET_KEYS:
                value = self.vault.encrypt(value)
            self.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    # --- Instanzen --------------------------------------------------------

    def instances(self, enabled_only: bool = False) -> list[Instance]:
        sql = "SELECT * FROM instances"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return [self._instance(r) for r in self.db.query(sql + " ORDER BY name")]

    def instance(self, instance_id: int) -> Instance | None:
        row = self.db.one("SELECT * FROM instances WHERE id = ?", (instance_id,))
        return self._instance(row) if row else None

    def instance_by_name(self, name: str) -> Instance | None:
        row = self.db.one("SELECT * FROM instances WHERE name = ?", (name,))
        return self._instance(row) if row else None

    def save_instance(
        self,
        instance_id: int | None,
        name: str,
        url: str,
        public_url: str,
        token: str,
        enabled: bool,
    ) -> int:
        url = url.rstrip("/")
        if instance_id:
            if token:
                self.db.execute(
                    "UPDATE instances SET token = ? WHERE id = ?",
                    (self.vault.encrypt(token), instance_id),
                )
            self.db.execute(
                "UPDATE instances SET name = ?, url = ?, public_url = ?, enabled = ? WHERE id = ?",
                (name, url, public_url, int(enabled), instance_id),
            )
            return instance_id
        return self.db.execute(
            "INSERT INTO instances (name, url, public_url, token, enabled) VALUES (?, ?, ?, ?, ?)",
            (name, url, public_url, self.vault.encrypt(token), int(enabled)),
        )

    def delete_instance(self, instance_id: int) -> None:
        self.db.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
        self.db.execute("DELETE FROM descriptions WHERE instance_id = ?", (instance_id,))

    def _instance(self, row: dict[str, Any]) -> Instance:
        return Instance(
            id=row["id"],
            name=row["name"],
            url=row["url"],
            public_url=row["public_url"],
            token=self.vault.decrypt(row["token"]),
            enabled=bool(row["enabled"]),
        )

    # --- Beschreibungen (Kriterien für Jev) ------------------------------

    def descriptions(self, instance_id: int) -> dict[tuple[str, int], dict[str, Any]]:
        rows = self.db.query(
            "SELECT kind, object_id, text, active FROM descriptions WHERE instance_id = ?",
            (instance_id,),
        )
        return {
            (r["kind"], r["object_id"]): {"text": r["text"], "active": bool(r["active"])}
            for r in rows
        }

    def save_description(
        self, instance_id: int, kind: str, object_id: int, text: str, active: bool
    ) -> None:
        self.db.execute(
            "INSERT INTO descriptions (instance_id, kind, object_id, text, active)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(instance_id, kind, object_id)"
            " DO UPDATE SET text = excluded.text, active = excluded.active",
            (instance_id, kind, object_id, text.strip(), int(active)),
        )
