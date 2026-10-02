"""Unabhängiger Vergleich der Klassifizierer mit erfundenen Dokumenten (bench/dokumente.py).

Stellt beiden Modellen genau die Fragen, die paperless-jev stellt (build_request),
und vergleicht mit der bekannten richtigen Antwort.

    python -m bench.run --jev                       # TYPESAFE_API_KEY aus der Umgebung
    python -m bench.run --ollama http://host:11434 --model clef
    python -m bench.run --jev --ollama http://host:11434   # beide, Tabelle nebeneinander
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import statistics
import time
from typing import Any

from paperless_jev.classifier import build_request, interpret
from paperless_jev.config import DEFAULTS
from paperless_jev.jev import classifier
from paperless_jev.paperless import Metadata

from .dokumente import CORRESPONDENTS, DOCUMENT_TYPES, DOCUMENTS, TAGS

FIELDS = ("document_type", "correspondent", "created")


def setup() -> tuple[Metadata, dict, dict[str, int], dict[str, int], dict[str, int]]:
    types = {i: n for i, n in enumerate(DOCUMENT_TYPES, start=1)}
    corrs = {i: n for i, n in enumerate(CORRESPONDENTS, start=100)}
    tags = {i: n for i, n in enumerate(TAGS, start=500)}
    meta = Metadata(correspondents=corrs, document_types=types, tags=tags)
    descriptions = {("document_type", i): {"text": DOCUMENT_TYPES[n], "active": True} for i, n in types.items()}
    descriptions |= {("tag", i): {"text": TAGS[n], "active": True} for i, n in tags.items()}
    rev = lambda d: {n: i for i, n in d.items()}  # noqa: E731
    return meta, descriptions, rev(types), rev(corrs), rev(tags)


async def run(cfg: dict[str, Any], label: str) -> list[dict[str, Any]]:
    meta, descriptions, type_id, corr_id, tag_id = setup()
    client, model = classifier(cfg)
    rows = []
    async with client:
        for n, d in enumerate(DOCUMENTS, start=1):
            doc = {"id": n, "content": d["text"], "original_file_name": f"scan_{n:03d}.pdf", "tags": [],
                   "document_type": None, "correspondent": None, "storage_path": None, "created": None}
            req = build_request(doc, meta, descriptions, cfg)
            t = time.monotonic()
            resp = await client.ask(req.state, req.questions, model)
            secs = time.monotonic() - t
            res = interpret(resp, req, meta, cfg)
            truth = {"document_type": type_id[d["document_type"]],
                     "correspondent": corr_id.get(d["correspondent"]) if d["correspondent"] else None,
                     "created": d["created"]}
            row: dict[str, Any] = {"doc": n, "secs": secs, "tokens": resp.get("usage", {}).get("input_tokens", 0), "fields": {}}
            for f in FIELDS:
                got = res["fields"].get(f) or {"value": None, "confidence": 0.0, "level": "none"}
                row["fields"][f] = {"ok": got["value"] == truth[f], "conf": got["confidence"], "level": got["level"],
                                    "got": got.get("label"), "want": d[f]}
            want = {tag_id[t] for t in d["tags"]}
            row["tags"] = {"want": sorted(want), "p": {t["id"]: t["p"] for t in res["tags"]}}
            rows.append(row)
            print(f"  {label} {n:2}/{len(DOCUMENTS)} {secs:5.1f}s", flush=True)
    return rows


def summary(rows: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"docs": len(rows), "secs": statistics.mean(r["secs"] for r in rows),
                           "tokens": sum(r["tokens"] for r in rows)}
    for f in FIELDS:
        vals = [r["fields"][f] for r in rows]
        right = [v["conf"] for v in vals if v["ok"]]
        wrong = [v["conf"] for v in vals if not v["ok"]]
        auto = [v for v in vals if v["level"] == "auto"]
        out[f] = {"right": len(right), "n": len(vals),
                  "conf_right": statistics.median(right) if right else None,
                  "conf_wrong": statistics.median(wrong) if wrong else None,
                  "auto": len(auto), "auto_wrong": sum(1 for v in auto if not v["ok"])}
    for name, threshold in (("tags_50", 0.5), ("tags_auto", float(cfg["fields"]["tags"]["auto"]))):
        tp = fp = fn = 0
        for r in rows:
            want = set(r["tags"]["want"])
            got = {int(t) for t, p in r["tags"]["p"].items() if p >= threshold}
            tp += len(got & want); fp += len(got - want); fn += len(want - got)
        out[name] = {"threshold": threshold, "tp": tp, "fp": fp, "fn": fn,
                     "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}
    return out


def table(results: dict[str, dict[str, Any]]) -> str:
    names = list(results)
    pct = lambda x: "–" if x is None else f"{x * 100:.0f} %"  # noqa: E731
    num = lambda x: "–" if x is None else f"{x:.2f}"  # noqa: E731
    lines = ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]

    def add(label: str, fn) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(results[n]) for n in names) + " |")

    for f, title in (("document_type", "Dokumenttyp"), ("correspondent", "Korrespondent"), ("created", "Datum")):
        add(f"{title}: richtig", lambda s, f=f: f"{s[f]['right']}/{s[f]['n']}")
        add(f"{title}: Confidence richtig / falsch (Median)", lambda s, f=f: f"{num(s[f]['conf_right'])} / {num(s[f]['conf_wrong'])}")
        add(f"{title}: automatisch gesetzt (davon falsch)", lambda s, f=f: f"{s[f]['auto']} ({s[f]['auto_wrong']})")
    for key, title in (("tags_50", "Tags ab 50 %"), ("tags_auto", "Tags ab Auto-Schwelle")):
        add(f"{title}: Präzision / Trefferquote", lambda s, k=key: f"{pct(s[k]['precision'])} / {pct(s[k]['recall'])}")
    add("Sekunden pro Dokument", lambda s: f"{s['secs']:.1f}")
    add("Input-Tokens total", lambda s: f"{s['tokens']:,}".replace(",", "'"))
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jev", action="store_true", help="TypeSafe Jev (TYPESAFE_API_KEY)")
    ap.add_argument("--jev-model", default="jev-latest")
    ap.add_argument("--ollama", help="Ollama-URL, z. B. http://192.168.0.16:11434")
    ap.add_argument("--model", default="clef", help="Ollama-Modell")
    ap.add_argument("--json", help="Rohdaten in diese Datei schreiben")
    args = ap.parse_args()
    base = copy.deepcopy(DEFAULTS) | {"examples": 0, "similar_docs": 0}
    runs: dict[str, dict[str, Any]] = {}
    if args.jev:
        runs[f"Jev ({args.jev_model})"] = base | {"classifier": "typesafe", "typesafe_api_key": os.environ["TYPESAFE_API_KEY"], "model": args.jev_model}
    if args.ollama:
        runs[f"Ollama ({args.model})"] = base | {"classifier": "ollama", "classifier_url": args.ollama, "classifier_model": args.model}
    if not runs:
        ap.error("--jev und/oder --ollama angeben")
    raw, results = {}, {}
    for label, cfg in runs.items():
        raw[label] = await run(cfg, label)
        results[label] = summary(raw[label], cfg)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"raw": raw, "summary": results}, fh, ensure_ascii=False, indent=1)
    print(f"\n{len(DOCUMENTS)} erfundene Dokumente, Standard-Schwellen von paperless-jev\n")
    print(table(results))


if __name__ == "__main__":
    asyncio.run(main())
