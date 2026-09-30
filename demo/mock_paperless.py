"""Nachgebautes Paperless-ngx mit erfundenen Daten – nur für Demo und Screenshots.

Liefert Stammdaten, Dokumente und generierte Vorschaubilder; schreibende Aufrufe
werden angenommen und verworfen. Start: uvicorn demo.mock_paperless:app --port 8001
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()


def _items(names: list[str], rules: dict[str, tuple[str, int]] | None = None) -> list[dict[str, Any]]:
    rules = rules or {}
    return [
        {"id": i, "name": n, "match": rules.get(n, ("", 0))[0], "matching_algorithm": rules.get(n, ("", 6))[1],
         "is_insensitive": True}
        for i, n in enumerate(names, start=1)
    ]


CORRESPONDENTS = _items(
    ["Bergquell Energie AG", "Seeland Versicherung", "Steuerverwaltung Kanton Muster", "Muster Bank AG",
     "Praxis Dr. Keller", "Telco Plus AG", "Velo Meier GmbH", "Gemeinde Musterdorf",
     "Hausverwaltung Sonnenhof", "Arbeitgeber Beispiel AG", "Garage Alpenblick", "Spital Seeland"],
    {"Seeland Versicherung": ("Police 1234567", 2), "Muster Bank AG": ("Muster Bank AG", 3),
     "Telco Plus AG": ("telcoplus.ch", 1)},
)
DOCUMENT_TYPES = _items(
    ["Rechnung", "Kontoauszug", "Vertrag", "Police", "Steuerbescheinigung", "Arztdokument", "Gutschrift",
     "Schreiben", "Lohnausweis", "Offerte"],
    {"Rechnung": ("Rechnung", 1), "Kontoauszug": ("Kontoauszug Saldo", 2)},
)
TAGS = _items(
    ["Posteingang", "Steuerrelevant", "Haus", "Auto", "Versicherung", "Gesundheit", "Bank",
     "ai-review", "ai-klassifiziert", "ai-ignorieren"],
    {"Auto": ("Garage Alpenblick", 1)},
)
TAGS[0]["is_inbox_tag"] = True

# (Titel, Typ, Korrespondent, Datum, Tags, im Posteingang, Farbe)
_DOCS = [
    ("Stromrechnung 3. Quartal", 1, 1, "2026-09-18", [3], True, "#f59e0b"),
    ("Prämienrechnung 2027", 1, 2, "2026-09-15", [5], True, "#2563eb"),
    ("Lohnausweis 2025", 9, 10, "2026-01-31", [2], False, "#16a34a"),
    ("Kontoauszug August", 2, 4, "2026-08-31", [7], False, "#0f766e"),
    ("Arztbericht Knie", 6, 5, "2026-09-02", [6], True, "#dc2626"),
    ("Mobile-Abo Vertrag", 3, 6, "2026-07-10", [], False, "#7c3aed"),
    ("Veloservice", 1, 7, "2026-08-22", [], False, "#ea580c"),
    ("Steuerveranlagung 2025", 5, 3, "2026-09-05", [2], True, "#475569"),
    ("Nebenkostenabrechnung", 1, 9, "2026-06-30", [3, 2], False, "#b45309"),
    ("Offerte Winterpneus", 10, 11, "2026-09-12", [4], True, "#0891b2"),
    ("Gutschrift Rückerstattung", 7, 2, "2026-08-14", [5, 6], False, "#15803d"),
    ("Informationsschreiben Abfall", 8, 8, "2026-05-20", [3], False, "#6b7280"),
    ("Austrittsbericht", 6, 12, "2026-04-11", [6], False, "#be123c"),
    ("Hypothekarvertrag", 3, 4, "2025-11-30", [3, 7, 2], False, "#1e40af"),
]
DOCUMENTS = [
    {"id": 100 + i, "title": t, "document_type": dt, "correspondent": c, "storage_path": None, "created": d,
     "tags": tags + ([1] if inbox else []), "content": f"{t}\n(Demodokument)", "original_file_name": f"scan_{100 + i}.pdf",
     "_inbox": inbox, "_color": color}
    for i, (t, dt, c, d, tags, inbox, color) in enumerate(_DOCS, start=1)
]
BY_ID = {d["id"]: d for d in DOCUMENTS}


def _page(results: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
    return {"count": len(clean), "next": None, "previous": None, "results": clean}


@app.get("/api/correspondents/")
def correspondents() -> dict[str, Any]:
    return _page(CORRESPONDENTS)


@app.get("/api/document_types/")
def document_types() -> dict[str, Any]:
    return _page(DOCUMENT_TYPES)


@app.get("/api/storage_paths/")
def storage_paths() -> dict[str, Any]:
    return _page([])


@app.get("/api/tags/")
def tags() -> dict[str, Any]:
    return _page(TAGS)


@app.get("/api/documents/")
def documents(request: Request) -> dict[str, Any]:
    q = request.query_params
    docs = DOCUMENTS
    if "is_in_inbox" in q:
        docs = [d for d in docs if d["_inbox"] == (q["is_in_inbox"] == "true")]
    for key in ("document_type", "correspondent"):
        if f"{key}__id" in q:
            docs = [d for d in docs if d[key] == int(q[f"{key}__id"])]
    if "tags__id__all" in q:
        docs = [d for d in docs if int(q["tags__id__all"]) in d["tags"]]
    if "more_like_id" in q:
        docs = [d for d in docs if d["id"] != int(q["more_like_id"])][:3]
    return _page(docs)


@app.get("/api/documents/{doc_id}/")
def document(doc_id: int) -> Response:
    doc = BY_ID.get(doc_id)
    if not doc:
        return JSONResponse({"detail": "No Document matches the given query."}, status_code=404)
    return JSONResponse({k: v for k, v in doc.items() if not k.startswith("_")})


@app.patch("/api/documents/{doc_id}/")
def patch(doc_id: int) -> dict[str, Any]:
    return {k: v for k, v in BY_ID.get(doc_id, {}).items() if not k.startswith("_")}


def _svg(doc: dict[str, Any]) -> str:
    lines = "".join(
        f'<rect x="26" y="{y}" width="{w}" height="7" rx="3" fill="#d6d9e0"/>'
        for y, w in [(150, 240), (166, 210), (182, 228), (198, 150), (230, 240), (246, 196), (262, 220), (278, 120)]
    )
    rows = "".join(
        f'<rect x="26" y="{y}" width="160" height="7" rx="3" fill="#e3e6ec"/><rect x="226" y="{y}" width="40" height="7" rx="3" fill="#c9cdd6"/>'
        for y in (318, 334, 350)
    )
    title = doc["title"].replace("&", "&amp;")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 420" width="300" height="420">
<rect width="300" height="420" fill="#fff"/>
<rect x="26" y="26" width="46" height="46" rx="8" fill="{doc["_color"]}"/>
<rect x="84" y="32" width="110" height="9" rx="4" fill="#9aa0ad"/><rect x="84" y="50" width="80" height="7" rx="3" fill="#c3c7d0"/>
<rect x="190" y="92" width="84" height="7" rx="3" fill="#c3c7d0"/>
<text x="26" y="128" font-family="Helvetica, Arial, sans-serif" font-size="17" font-weight="700" fill="#1f2430">{title}</text>
{lines}{rows}
<rect x="186" y="378" width="80" height="10" rx="4" fill="{doc["_color"]}" opacity=".75"/>
</svg>'''


@app.get("/api/documents/{doc_id}/thumb/")
def thumb(doc_id: int) -> Response:
    doc = BY_ID.get(doc_id)
    if not doc:
        return Response(status_code=404)
    return Response(_svg(doc), media_type="image/svg+xml")


def _pdf(doc: dict[str, Any]) -> bytes:
    """Einfaches einseitiges PDF mit Textebene (für pdf.js und die Markierungen)."""
    corr = next(c["name"] for c in CORRESPONDENTS if c["id"] == doc["correspondent"])
    dtype = next(t["name"] for t in DOCUMENT_TYPES if t["id"] == doc["document_type"])
    y, m, d = doc["created"].split("-")
    lines = [(18, 60, 780, corr), (9, 60, 764, "Seestrasse 12, 3000 Musterstadt"),
             (10, 360, 700, "Familie Muster"), (10, 360, 686, "Dorfweg 4, 3000 Musterstadt"),
             (15, 60, 620, f"{dtype} Nr. 2026-{doc['id']}"), (10, 60, 598, f"Datum: {d}.{m}.{y}"),
             (10, 60, 584, f"Betreff: {doc['title']}"), (10, 60, 540, "Position                                   Betrag CHF"),
             (10, 60, 522, "Leistung gemaess Vereinbarung                  142.30"),
             (10, 60, 506, "Zuschlaege und Abgaben                          21.60"),
             (11, 60, 480, "Total CHF 163.90"), (9, 60, 440, "Zahlbar innert 30 Tagen. Besten Dank fuer Ihren Auftrag.")]
    esc = lambda t: t.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = "".join(f"BT /F1 {size} Tf {x} {yy} Td ({esc(text)}) Tj ET\n" for size, x, yy, text in lines).encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


@app.get("/api/documents/{doc_id}/preview/")
def preview(doc_id: int) -> Response:
    doc = BY_ID.get(doc_id)
    if not doc:
        return Response(status_code=404)
    return Response(_pdf(doc), media_type="application/pdf")


_polls: dict[str, int] = {}


@app.post("/api/documents/post_document/")
async def post_document(request: Request) -> str:
    form = await request.form()
    upload = form.get("document")
    print(f"Upload: {getattr(upload, 'filename', '?')} {len(await upload.read())} Bytes, tags={form.getlist('tags')}", flush=True)
    _polls["demo-task"] = 0
    return "demo-task"


@app.get("/api/tasks/")
def tasks(task_id: str) -> list[dict[str, Any]]:
    # nach zwei Abfragen "fertig" - wie ein kurzer OCR-Lauf
    _polls[task_id] = _polls.get(task_id, 0) + 1
    done = _polls[task_id] > 2
    return [{"task_id": task_id, "status": "SUCCESS" if done else "STARTED", "related_document": "110" if done else None}]


@app.post("/api/tags/")
def create_tag() -> dict[str, Any]:
    return {"id": 999}
