"""Phase-0-Probelauf: Dokumente klassifizieren und mit dem Ist-Zustand vergleichen.

Schreibt nichts nach Paperless. Beispiel:

    PAPERLESS_URL=http://paperless:8000 PAPERLESS_TOKEN=... \\
    TYPESAFE_API_KEY=... paperless-jev-spike --limit 20 --json spike.json

Beschreibungen lassen sich vorab als JSON-Datei testen, Schlüssel sind die
Namen aus Paperless:

    {"document_type": {"Kontoauszug": "Bank documents incl. credit/debit advices"},
     "tag": {"Steuerrelevant": "Relevant for the annual tax return"}}
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from typing import Any

from .classifier import build_request, excluded_tags, interpret
from .config import DEFAULTS, SINGLE_FIELDS
from .jev import USD_PER_MTOK, JevClient
from .paperless import Metadata, PaperlessClient, collect_examples


def load_descriptions(path: str | None, meta: Metadata) -> dict[tuple[str, int], dict[str, Any]]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for kind, entries in raw.items():
        by_name = {name.lower(): oid for oid, name in meta.names(kind).items()}
        for name, text in entries.items():
            oid = by_name.get(name.lower())
            if oid is None:
                print(f"Warnung: {kind} '{name}' gibt es in Paperless nicht", file=sys.stderr)
                continue
            active = text is not False
            result[(kind, oid)] = {"text": text if isinstance(text, str) else "", "active": active}
    return result


async def run(args: argparse.Namespace) -> int:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["model"] = args.model
    cfg["examples"] = args.examples
    cfg["fields"]["storage_path"]["enabled"] = args.storage_paths
    rows = []
    tokens = 0
    agree: dict[str, list[int]] = {name: [0, 0] for name in SINGLE_FIELDS}
    async with (
        PaperlessClient(args.url, args.token, host=args.host) as pl,
        JevClient(args.api_key) as jev,
    ):
        meta = await pl.metadata()
        descriptions = load_descriptions(args.descriptions, meta)
        kinds = ["document_type", "tag"] + (["storage_path"] if args.storage_paths else [])
        examples = (
            await collect_examples(pl, meta, kinds, args.examples, excluded_tags(meta, cfg))
            if args.examples > 0 else {}
        )
        doc_ids = args.doc or (await pl.inbox_document_ids())[: args.limit]
        print(f"{len(doc_ids)} Dokument(e), {len(meta.document_types)} Typen, "
              f"{len(meta.correspondents)} Korrespondenten, {len(meta.tags)} Tags, "
              f"Beispiele für {len(examples)} Einträge\n", file=sys.stderr)
        for doc_id in doc_ids:
            doc = await pl.document(doc_id)
            similar = await pl.similar_documents(doc_id, 3) if args.similar else []
            req = build_request(doc, meta, descriptions, cfg, similar, examples)
            resp = await jev.ask(req.state, req.questions, cfg["model"])
            result = interpret(resp, req, meta, cfg)
            tokens += result["usage"].get("input_tokens", 0)
            rows.append({"id": doc_id, "title": doc.get("title"), "doc": {k: v for k, v in doc.items() if k != "content"},
                         "result": result, "request": {"questions": req.questions}})
            print(f"#{doc_id} {doc.get('title')}")
            for name in SINGLE_FIELDS:
                f = result["fields"].get(name)
                if not f:
                    continue
                cur_id = doc.get(name)
                cur = cur_id if name == "created" else (meta.names(name).get(cur_id) if cur_id else None)
                if cur is None:
                    mark = ""
                else:
                    hit = f["value"] == cur_id
                    agree[name][0] += hit
                    agree[name][1] += 1
                    mark = "  ✓" if hit else f"  (aktuell: {cur})"
                print(f"   {name:<14} {f['label']!s:<35} conf {f['confidence']:.2f}  [{f['level']}]{mark}")
            tags = [f"{t['label']} {t['p']:.2f}" for t in result["tags"] if t["level"] != "low"]
            current_tags = [meta.tags.get(t, str(t)) for t in doc.get("tags", []) if t not in meta.inbox_tags]
            print(f"   tags neu       {', '.join(tags) or '–'}")
            print(f"   tags aktuell   {', '.join(current_tags) or '–'}\n")

    print("Übereinstimmung mit dem Ist-Zustand:", file=sys.stderr)
    for name, (hits, total) in agree.items():
        if total:
            print(f"   {name:<14} {hits}/{total}", file=sys.stderr)
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
    p.add_argument("--examples", type=int, default=5, help="Beispieltitel je Typ/Tag (0 = aus)")
    p.add_argument("--descriptions", help="JSON-Datei mit Beschreibungen (siehe Modul-Doku)")
    p.add_argument("--similar", action="store_true", help="ähnliche Dokumente als Kontext mitschicken")
    p.add_argument("--storage-paths", action="store_true", help="auch Speicherpfade bestimmen")
    p.add_argument("--json", help="Ergebnisse inkl. gestellter Fragen als JSON speichern")
    args = p.parse_args()
    missing = [n for n in ("url", "token", "api_key") if not getattr(args, n)]
    if missing:
        p.error("fehlt: " + ", ".join(missing))
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
