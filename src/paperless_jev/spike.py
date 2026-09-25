"""Phase-0-Probelauf: Posteingang klassifizieren und als Tabelle ausgeben.

Schreibt nichts nach Paperless. Beispiel:

    PAPERLESS_URL=http://paperless:8000 PAPERLESS_TOKEN=... \\
    TYPESAFE_API_KEY=... paperless-jev-spike --limit 20 --json spike.json
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys

from .classifier import build_request, interpret
from .config import DEFAULTS, SINGLE_FIELDS
from .jev import USD_PER_MTOK, JevClient
from .paperless import PaperlessClient


async def run(args: argparse.Namespace) -> int:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["model"] = args.model
    cfg["fields"]["storage_path"]["enabled"] = args.storage_paths
    rows = []
    tokens = 0
    async with (
        PaperlessClient(args.url, args.token, host=args.host) as pl,
        JevClient(args.api_key) as jev,
    ):
        meta = await pl.metadata()
        doc_ids = args.doc or (await pl.inbox_document_ids())[: args.limit]
        print(f"{len(doc_ids)} Dokument(e), {len(meta.document_types)} Typen, "
              f"{len(meta.correspondents)} Korrespondenten, {len(meta.tags)} Tags\n", file=sys.stderr)
        for doc_id in doc_ids:
            doc = await pl.document(doc_id)
            similar = await pl.similar_documents(doc_id, 3) if args.similar else []
            req = build_request(doc, meta, {}, cfg, similar)
            resp = await jev.ask(req.state, req.questions, cfg["model"])
            result = interpret(resp, req, meta, cfg)
            tokens += result["usage"].get("input_tokens", 0)
            current = {
                "document_type": meta.document_types.get(doc.get("document_type")),
                "correspondent": meta.correspondents.get(doc.get("correspondent")),
                "created": doc.get("created"),
            }
            rows.append({"id": doc_id, "title": doc.get("title"), "current": current, "result": result})
            print(f"#{doc_id} {doc.get('title')}")
            for name in SINGLE_FIELDS:
                f = result["fields"].get(name)
                if f:
                    cur = current.get(name)
                    mark = "" if cur is None else ("  ✓" if cur in (f["label"], f["value"]) else f"  (aktuell: {cur})")
                    print(f"   {name:<14} {f['label']!s:<35} conf {f['confidence']:.2f}  [{f['level']}]{mark}")
            tags = [f"{t['label']} {t['p']:.2f}" for t in result["tags"] if t["level"] != "low"]
            if tags:
                print(f"   tags           {', '.join(tags)}")
            print()
    print(f"Tokens gesamt: {tokens} (≈ ${tokens / 1e6 * USD_PER_MTOK:.5f})", file=sys.stderr)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2, default=str)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Probelauf: Paperless-Dokumente mit Jev klassifizieren (schreibt nichts).")
    p.add_argument("--url", default=os.environ.get("PAPERLESS_URL"), help="Paperless-URL")
    p.add_argument("--token", default=os.environ.get("PAPERLESS_TOKEN"), help="Paperless-API-Token")
    p.add_argument("--host", default=os.environ.get("PAPERLESS_HOST"),
                   help="öffentlicher Hostname als Host-Header (bei Zugriff über den Containernamen)")
    p.add_argument("--api-key", default=os.environ.get("TYPESAFE_API_KEY"), help="TypeSafe-API-Key")
    p.add_argument("--model", default="jev-latest")
    p.add_argument("--limit", type=int, default=20, help="max. Dokumente aus dem Posteingang")
    p.add_argument("--doc", type=int, action="append", help="bestimmte Dokument-ID(s) statt Posteingang")
    p.add_argument("--similar", action="store_true", help="ähnliche Dokumente als Kontext mitschicken")
    p.add_argument("--storage-paths", action="store_true", help="auch Speicherpfade bestimmen")
    p.add_argument("--json", help="Ergebnisse zusätzlich als JSON speichern")
    args = p.parse_args()
    missing = [n for n in ("url", "token", "api_key") if not getattr(args, n)]
    if missing:
        p.error("fehlt: " + ", ".join(missing))
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
