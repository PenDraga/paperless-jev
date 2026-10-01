"""Füllt eine Demo-Datenbank mit erfundenen Ergebnissen (passend zu demo/mock_paperless.py).

    PJ_DATA_DIR=./demo-data python -m demo.seed
"""

from __future__ import annotations

import json
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from paperless_jev.config import Config
from paperless_jev.db import Database
from paperless_jev.vault import Vault

from .mock_paperless import BY_ID, CORRESPONDENTS, DOCUMENT_TYPES, TAGS

DATA = Path(os.environ.get("PJ_DATA_DIR", "./demo-data"))
TYPES = {d["id"]: d["name"] for d in DOCUMENT_TYPES}
CORRS = {c["id"]: c["name"] for c in CORRESPONDENTS}
TAGN = {t["id"]: t["name"] for t in TAGS}
rng = random.Random(3)


def level(conf: float, auto: float, review: float) -> str:
    return "auto" if conf >= auto else "suggest" if conf >= review else "low"


def field(names: dict[int, str], value: int | None, conf: float, alts: list[int] = ()) -> dict:
    top = [[names[value], conf]] + [[names[a], round((1 - conf) / (len(alts) + 1), 2)] for a in alts]
    return {"value": value, "label": names[value], "confidence": conf, "level": level(conf, 0.85, 0.4), "top": top}


def date_field(value: str, conf: float) -> dict:
    label = datetime.fromisoformat(value).strftime("%d.%m.%Y")
    return {"value": value, "label": label, "confidence": conf, "level": level(conf, 0.9, 0.5), "top": [[label, conf]]}


def result(dt: int, dt_conf: float, corr: int, corr_conf: float, created: str, date_conf: float,
           tags: list[tuple[int, float]], current: dict, alts: tuple = ((), ())) -> dict:
    return {
        "model": "jev-1.13.0",
        "usage": {"input_tokens": rng.randint(7000, 13000), "output_tokens": 0},
        "fields": {
            "document_type": field(TYPES, dt, dt_conf, list(alts[0])),
            "correspondent": field(CORRS, corr, corr_conf, list(alts[1])),
            "created": date_field(created, date_conf),
        },
        # wie im echten Betrieb: nur Tags vorschlagen, die das Dokument noch nicht hat
        "tags": [{"id": t, "label": TAGN[t], "p": p, "level": level(p, 0.9, 0.7)} for t, p in tags if t not in current.get("tags", [])],
        "plan": {"updates": {}, "needs_review": False},
        "question_count": 14,
        "current": current,
    }


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for f in ("paperless-jev.db", "secret.key"):
        (DATA / f).unlink(missing_ok=True)
    db = Database(DATA / "paperless-jev.db")
    cfg = Config(db, Vault(DATA))
    cfg.update({"typesafe_api_key": "demo", "mode": "auto", "poll_minutes": 0, "model": "jev-1.13.0",
                "llm_provider": "ollama", "llm_url": "http://ollama:11434", "llm_model": "qwen3:8b",
                "title_mode": "llm", "webhook_secret": "demo"})
    cfg.save_instance(None, "zuhause", "http://127.0.0.1:8001", "https://paperless.example.com", "demo", True)
    inst = cfg.instances()[0].id

    for kind, oid, source, text in [
        ("document_type", 6, "Arzt, Spital, Labor, Arztzeugnis – nicht: Krankenkassenpolice",
         "Berichte und Belege von Ärzten, Spitälern und Labors wie Arztberichte, Laborbefunde und Arztzeugnisse. Nicht: Krankenkassenpolicen."),
        ("document_type", 7, "Gutschrift, Rückerstattung, Dividende – nicht: Gutschriftsanzeige der Bank",
         "Gutschriften und Rückerstattungen von Händlern, Versicherungen oder Firmen, auch Dividenden. Nicht: Gutschriftsanzeigen der Bank."),
        ("document_type", 2, "Bankbelege, Kontoauszug, Belastung, Gutschriftsanzeige",
         "Bankbelege zu Kontobewegungen wie Kontoauszüge, Belastungs- und Gutschriftsanzeigen."),
        ("tag", 2, "Lohnausweis, Steuerbescheinigung, Säule 3a, Spenden – nicht: normale Kontoauszüge",
         "Dokumente für die Steuererklärung wie Lohnausweise, Steuerbescheinigungen, Säule-3a-Belege und Spendenbestätigungen. Nicht: normale Kontoauszüge."),
    ]:
        cfg.save_description(inst, kind, oid, text, True, source=source)

    t0 = datetime.now(UTC) - timedelta(days=21)
    rows: list[tuple] = []

    # Verlauf: bereits verarbeitete Dokumente (für Kennzahlen und Auswertung)
    for i in range(150):
        dt, corr = rng.choice(list(TYPES)), rng.choice(list(CORRS))
        ok = rng.random() < 0.9
        corr_cur = corr if rng.random() < 0.8 else (None if rng.random() < 0.5 else rng.choice(list(CORRS)))
        cur = {"document_type": dt if ok else rng.choice(list(TYPES)), "correspondent": corr_cur,
               "storage_path": None, "created": "2026-05-01" if rng.random() < 0.93 else "2026-04-28", "tags": []}
        conf = rng.choice([0.97, 0.95, 0.92, 0.88, 0.99, 0.72, 0.64])
        res = result(dt, conf, corr, rng.choice([0.98, 0.94, 0.9, 0.81, 0.58]), "2026-05-01", 0.95,
                     [(rng.choice([2, 3, 4, 5, 6, 7]), rng.choice([0.95, 0.93, 0.78]))], cur)
        status = "done" if i % 9 else "dry_run"
        source = "poll+review" if i % 5 == 0 else "webhook"
        applied = {"corrections": {"correspondent": 1}} if i % 15 == 0 else ({"fields": {}} if source.endswith("+review") else None)
        rows.append((2000 + i, f"Dokument {2000 + i}", source, status, res, applied, t0 + timedelta(hours=3 * i)))

    # Die zuletzt verarbeiteten Beispiel-Dokumente (erscheinen oben in der Liste)
    def cur(doc_id: int, keep: bool = True) -> dict:
        d = BY_ID[doc_id]
        return {"document_type": d["document_type"] if keep else None, "correspondent": d["correspondent"] if keep else None,
                "storage_path": None, "created": d["created"], "tags": [t for t in d["tags"] if t != 1]}

    curated = [
        (114, "done", "webhook", result(3, 0.96, 4, 0.99, "2025-11-30", 0.97, [(3, 0.95), (7, 0.91)], cur(114))),
        (113, "done", "webhook", result(6, 0.94, 12, 0.97, "2026-04-11", 0.96, [(6, 0.97)], cur(113))),
        (112, "done", "poll", result(8, 0.88, 8, 0.99, "2026-05-20", 0.93, [(3, 0.92)], cur(112))),
        (111, "done", "webhook+review", result(7, 0.91, 2, 0.95, "2026-08-14", 0.94, [(5, 0.93), (6, 0.76)], cur(111))),
        (109, "done", "webhook", result(1, 0.97, 9, 0.96, "2026-06-30", 0.95, [(3, 0.94), (2, 0.91)], cur(109))),
        (107, "done", "webhook", result(1, 0.98, 7, 0.93, "2026-08-22", 0.97, [], cur(107))),
        (106, "done", "poll", result(3, 0.93, 6, 0.99, "2026-07-10", 0.96, [], cur(106))),
        (104, "done", "webhook", result(2, 0.99, 4, 0.99, "2026-08-31", 0.98, [(7, 0.96)], cur(104))),
        (103, "done", "webhook", result(9, 0.99, 10, 0.97, "2026-01-31", 0.99, [(2, 0.98)], cur(103))),
        (110, "done", "webhook", result(10, 0.95, 11, 0.98, "2026-09-12", 0.94, [(4, 0.97)], cur(110, False))),
        (108, "review", "webhook", result(5, 0.83, 3, 0.96, "2026-09-05", 0.91, [(2, 0.88)], cur(108, False), ((8,), ()))),
        (105, "review", "webhook", result(6, 0.71, 5, 0.92, "2026-09-02", 0.89, [(6, 0.95), (5, 0.74)], cur(105, False), ((8, 4), ()))),
        (102, "review", "webhook", result(1, 0.97, 2, 0.62, "2026-09-15", 0.96, [(5, 0.93)], cur(102, False), ((), (1,)))),
        (101, "review", "webhook", result(1, 0.98, 1, 0.99, "2026-09-18", 0.97, [(11, 0.86)], cur(101, False))),
    ]
    now = datetime.now(UTC) - timedelta(hours=2)
    for n, (doc_id, status, source, res) in enumerate(curated):
        rows.append((doc_id, BY_ID[doc_id]["title"], source, status, res, None, now + timedelta(minutes=7 * n)))

    for doc_id, title, source, status, res, applied, ts in rows:
        job = db.create_job(inst, doc_id, source)
        db.update_job(job, doc_title=title, status=status, result=res, applied=applied, model=res["model"],
                      input_tokens=res["usage"]["input_tokens"])
        stamp = ts.isoformat(timespec="seconds")
        db.execute("UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?", (stamp, stamp, job))
    print(f"Demo-Daten in {DATA}: {len(rows)} Jobs")


if __name__ == "__main__":
    main()
