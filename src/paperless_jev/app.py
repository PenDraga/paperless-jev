"""Web-UI, Webhook-Endpunkt und Start der Hintergrundverarbeitung."""

from __future__ import annotations

import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .classifier import excluded_tags
from .config import FIELD_LABELS, SINGLE_FIELDS, Config
from .db import Database
from .jev import USD_PER_MTOK, JevClient, JevError
from .paperless import PaperlessClient, PaperlessError
from .processor import Processor
from .vault import Vault

log = logging.getLogger("paperless_jev")
HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("PJ_DATA_DIR", "/data"))
ADMIN_USER = os.environ.get("PJ_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("PJ_ADMIN_PASSWORD", "")
PUBLIC_PATHS = ("/hook/", "/health", "/static/")

STATUS_LABELS = {
    "queued": "wartet",
    "running": "läuft",
    "dry_run": "Probelauf",
    "review": "Review",
    "done": "erledigt",
    "dismissed": "verworfen",
    "skipped": "übersprungen",
    "error": "Fehler",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = Database(DATA_DIR / "paperless-jev.db")
    config = Config(db, Vault(DATA_DIR))
    processor = Processor(db, config)
    app.state.db, app.state.config, app.state.processor = db, config, processor
    await processor.start()
    yield
    await processor.stop()


app = FastAPI(title="paperless-jev", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals.update(
    FIELD_LABELS=FIELD_LABELS, STATUS_LABELS=STATUS_LABELS, version=__version__
)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Optionaler Passwortschutz; hinter Traefik + Authentik meist nicht nötig."""
    if ADMIN_PASSWORD and not request.url.path.startswith(PUBLIC_PATHS):
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, ADMIN_USER) and secrets.compare_digest(pw, ADMIN_PASSWORD)
            except ValueError:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="paperless-jev"'})
    return await call_next(request)


def _cfg(request: Request) -> Config:
    return request.app.state.config


def _proc(request: Request) -> Processor:
    return request.app.state.processor


def _render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("msg", request.query_params.get("msg"))
    ctx.setdefault("err", request.query_params.get("err"))
    return templates.TemplateResponse(request, name, ctx)


def _redirect(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    from urllib.parse import urlencode

    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    return RedirectResponse(f"{url}?{urlencode(params)}" if params else url, status_code=303)


# --- Dashboard und Log ---------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db: Database = request.app.state.db
    cfg = _cfg(request).all()
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    tokens = db.one("SELECT COALESCE(SUM(input_tokens), 0) AS t FROM jobs")["t"]
    corrections: dict[str, int] = {}
    reviewed = 0
    for row in db.query("SELECT applied FROM jobs WHERE source LIKE '%+review' AND applied IS NOT NULL"):
        reviewed += 1
        for name in row["applied"].get("corrections", {}):
            corrections[name] = corrections.get(name, 0) + 1
    finished = counts.get("done", 0)
    auto = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'done' AND source NOT LIKE '%+review'")["n"]
    return _render(
        request,
        "dashboard.html",
        cfg=cfg,
        instances=_cfg(request).instances(),
        counts=counts,
        tokens=tokens,
        cost=tokens / 1_000_000 * USD_PER_MTOK,
        auto_rate=(auto / finished) if finished else None,
        reviewed=reviewed,
        corrections=corrections,
        queue_size=_proc(request).queue.qsize(),
        recent=_jobs(request, limit=10),
    )


def _jobs(request: Request, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    db: Database = request.app.state.db
    names = {i.id: i for i in _cfg(request).instances()}
    sql, params = "SELECT * FROM jobs", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    rows = db.query(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
    for row in rows:
        row["instance"] = names.get(row["instance_id"])
    return rows


@app.get("/log", response_class=HTMLResponse)
async def job_log(request: Request, status: str | None = None):
    return _render(request, "log.html", jobs=_jobs(request, status or None, limit=300), status=status)


@app.post("/run")
async def run_now(request: Request, instance_id: int = Form(0), force: bool = Form(False)):
    count = await _proc(request).poll_once(instance_id or None, force=force)
    return _redirect("/", msg=f"{count} Dokument(e) eingereiht")


@app.post("/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: int):
    job = request.app.state.db.job(job_id)
    if not job:
        raise HTTPException(404)
    new_id = await _proc(request).enqueue(job["instance_id"], job["doc_id"], "manual", force=True)
    return _redirect(f"/jobs/{new_id}" if new_id else f"/jobs/{job_id}", msg="Neu eingereiht")


# --- Review --------------------------------------------------------------


async def _review_context(request: Request, job: dict[str, Any]) -> dict[str, Any]:
    """Optionen für die Auswahlfelder: Stammdaten der Instanz des Jobs."""
    inst = _cfg(request).instance(job["instance_id"])
    job["instance"] = inst
    if not inst:
        return {"meta": None}
    try:
        async with _proc(request).client(inst) as pl:
            meta = await _proc(request).metadata(inst, pl)
    except PaperlessError as e:
        return {"meta": None, "meta_error": str(e)}
    hidden = excluded_tags(meta, _cfg(request).all())
    return {"meta": meta, "tag_options": {k: v for k, v in meta.tags.items() if k not in hidden}}


@app.get("/review", response_class=HTMLResponse)
async def review_queue(request: Request):
    items = []
    for job in _jobs(request, "review", limit=50):
        items.append({"job": job, **await _review_context(request, job)})
    return _render(request, "review.html", items=items)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int):
    job = request.app.state.db.job(job_id)
    if not job:
        raise HTTPException(404)
    return _render(request, "job.html", job=job, **await _review_context(request, job))


@app.post("/jobs/{job_id}/apply")
async def apply_job(request: Request, job_id: int):
    form = await request.form()
    if form.get("action") == "dismiss":
        await _proc(request).dismiss(job_id)
        return _redirect(str(form.get("back") or "/review"), msg="Verworfen")
    choice: dict[str, Any] = {}
    for name in SINGLE_FIELDS:
        if f"use_{name}" not in form:
            continue
        raw = str(form.get(name) or "")
        if name == "created":
            if raw:
                choice[name] = raw
        else:
            choice[name] = int(raw) if raw else None
    choice["tags_add"] = [int(t) for t in form.getlist("tags")]
    try:
        await _proc(request).apply_review(job_id, choice)
    except PaperlessError as e:
        return _redirect(f"/jobs/{job_id}", err=str(e))
    return _redirect(str(form.get("back") or "/review"), msg="Übernommen")


@app.get("/thumb/{instance_id}/{doc_id}")
async def thumbnail(request: Request, instance_id: int, doc_id: int):
    inst = _cfg(request).instance(instance_id)
    if not inst:
        raise HTTPException(404)
    try:
        async with _proc(request).client(inst) as pl:
            content, ctype = await pl.thumbnail(doc_id)
    except PaperlessError:
        raise HTTPException(404) from None
    return Response(content, media_type=ctype, headers={"Cache-Control": "max-age=3600"})


# --- Setup ----------------------------------------------------------------


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    cfg = _cfg(request).all()
    return _render(
        request,
        "setup.html",
        cfg=cfg,
        has_key=bool(cfg["typesafe_api_key"]),
        instances=_cfg(request).instances(),
        base_url=str(request.base_url).rstrip("/"),
    )


@app.post("/setup/typesafe")
async def save_typesafe(request: Request, api_key: str = Form(""), model: str = Form("jev-latest")):
    values: dict[str, Any] = {"model": model.strip() or "jev-latest"}
    if api_key.strip():
        values["typesafe_api_key"] = api_key.strip()
    _cfg(request).update(values)
    return _redirect("/setup", msg="TypeSafe-Einstellungen gespeichert")


@app.post("/setup/typesafe/test", response_class=HTMLResponse)
async def test_typesafe(request: Request):
    cfg = _cfg(request).all()
    if not cfg["typesafe_api_key"]:
        return HTMLResponse('<span class="bad">Kein API-Key gespeichert</span>')
    try:
        async with JevClient(cfg["typesafe_api_key"], timeout=20) as jev:
            resp = await jev.ask(
                "Invoice no. 4711 from Stadtwerke, amount due EUR 84.20",
                {"t": {"type": "choice", "instructions": "What type of document is this?",
                       "criteria": {"invoice": None, "letter": None}}},
                cfg["model"],
            )
    except JevError as e:
        return HTMLResponse(f'<span class="bad">{e}</span>')
    ans = resp["answers"]["t"]
    return HTMLResponse(
        f'<span class="good">OK - {resp.get("model")} antwortet "{ans["choice"]}" '
        f'(Confidence {ans["confidence"]:.2f})</span>'
    )


@app.post("/setup/instances")
async def save_instance(
    request: Request,
    instance_id: int = Form(0),
    name: str = Form(...),
    url: str = Form(...),
    public_url: str = Form(""),
    token: str = Form(""),
    enabled: bool = Form(False),
):
    name = name.strip().lower().replace(" ", "-")
    if not instance_id and not token.strip():
        return _redirect("/setup", err="Für eine neue Instanz wird ein Token benötigt")
    _cfg(request).save_instance(instance_id or None, name, url.strip(), public_url.strip(), token.strip(), enabled)
    _proc(request).forget_metadata(instance_id or None)
    return _redirect("/setup", msg=f"Instanz {name} gespeichert")


@app.post("/setup/instances/{instance_id}/delete")
async def delete_instance(request: Request, instance_id: int):
    _cfg(request).delete_instance(instance_id)
    return _redirect("/setup", msg="Instanz gelöscht")


@app.post("/setup/instances/{instance_id}/test", response_class=HTMLResponse)
async def test_instance(request: Request, instance_id: int):
    inst = _cfg(request).instance(instance_id)
    if not inst:
        return HTMLResponse('<span class="bad">Unbekannte Instanz</span>')
    try:
        async with PaperlessClient(inst.url, inst.token, timeout=10, host=inst.host_header) as pl:
            inbox = await pl.ping()
            meta = await pl.metadata()
    except PaperlessError as e:
        return HTMLResponse(f'<span class="bad">{e}</span>')
    return HTMLResponse(
        f'<span class="good">OK - {inbox} im Posteingang, {len(meta.document_types)} Typen, '
        f"{len(meta.correspondents)} Korrespondenten, {len(meta.tags)} Tags</span>"
    )


# --- Regeln ----------------------------------------------------------------


@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request):
    return _render(request, "rules.html", cfg=_cfg(request).all())


@app.post("/rules")
async def save_rules(request: Request):
    form = await request.form()
    cfg = _cfg(request).all()

    def num(key: str, default: float) -> float:
        try:
            return float(str(form.get(key, default)).replace(",", "."))
        except ValueError:
            return default

    fields = {}
    for name, spec in cfg["fields"].items():
        auto = min(max(num(f"{name}_auto", spec["auto"]), 0.0), 1.0)
        review = min(max(num(f"{name}_review", spec["review"]), 0.0), auto)
        fields[name] = {"enabled": f"{name}_enabled" in form, "auto": auto, "review": review}
    mode = str(form.get("mode", "dry_run"))
    _cfg(request).update(
        {
            "mode": mode if mode in ("dry_run", "review", "auto") else "dry_run",
            "poll_minutes": int(num("poll_minutes", 5)),
            "max_chars": max(int(num("max_chars", 12000)), 1000),
            "similar_docs": max(int(num("similar_docs", 3)), 0),
            "examples": max(int(num("examples", 5)), 0),
            "tags_force_review": "tags_force_review" in form,
            "overwrite": "overwrite" in form,
            "remove_inbox": "remove_inbox" in form,
            "tag_done": str(form.get("tag_done") or cfg["tag_done"]).strip(),
            "tag_review": str(form.get("tag_review") or cfg["tag_review"]).strip(),
            "tag_ignore": str(form.get("tag_ignore") or cfg["tag_ignore"]).strip(),
            "title_enabled": "title_enabled" in form,
            "title_template": str(form.get("title_template") or cfg["title_template"]),
            "fields": fields,
        }
    )
    _proc(request).forget_metadata()
    return _redirect("/rules", msg="Regeln gespeichert")


# --- Beschreibungen ------------------------------------------------------

KINDS = {
    "document_type": "Dokumenttypen",
    "correspondent": "Korrespondenten",
    "tag": "Tags",
    "storage_path": "Speicherpfade",
}


@app.get("/descriptions", response_class=HTMLResponse)
async def descriptions_page(request: Request, instance_id: int = 0, kind: str = "document_type"):
    instances = _cfg(request).instances()
    inst = next((i for i in instances if i.id == instance_id), instances[0] if instances else None)
    kind = kind if kind in KINDS else "document_type"
    rows, error = [], None
    if inst:
        try:
            async with _proc(request).client(inst) as pl:
                meta = await _proc(request).metadata(inst, pl, fresh=True)
            stored = _cfg(request).descriptions(inst.id)
            hidden = excluded_tags(meta, _cfg(request).all()) if kind == "tag" else set()
            for oid, name in sorted(meta.names(kind).items(), key=lambda kv: kv[1].lower()):
                if oid in hidden:
                    continue
                d = stored.get((kind, oid), {"text": "", "active": True})
                rows.append({"id": oid, "name": name, **d})
        except PaperlessError as e:
            error = str(e)
    return _render(
        request, "descriptions.html",
        instances=instances, inst=inst, kind=kind, kinds=KINDS, rows=rows, meta_error=error,
    )


@app.post("/descriptions")
async def save_descriptions(request: Request):
    form = await request.form()
    instance_id = int(str(form["instance_id"]))
    kind = str(form["kind"])
    for oid in form.getlist("ids"):
        _cfg(request).save_description(
            instance_id, kind, int(oid), str(form.get(f"text_{oid}", "")), f"active_{oid}" in form
        )
    return _redirect(f"/descriptions?instance_id={instance_id}&kind={kind}", msg="Beschreibungen gespeichert")


# --- Webhook und Health ------------------------------------------------------


@app.post("/hook/{instance_name}")
async def webhook(request: Request, instance_name: str, token: str = ""):
    cfg = _cfg(request).all()
    if not secrets.compare_digest(token, cfg["webhook_secret"]):
        raise HTTPException(403, "Ungültiges Token")
    inst = _cfg(request).instance_by_name(instance_name)
    if not inst or not inst.enabled:
        raise HTTPException(404, "Unbekannte oder deaktivierte Instanz")
    doc_id: Any = None
    if "json" in request.headers.get("content-type", ""):
        body = await request.json()
        doc_id = body.get("doc_id") if isinstance(body, dict) else None
    else:
        doc_id = (await request.form()).get("doc_id")
    try:
        doc_id = int(str(doc_id).strip())
    except (TypeError, ValueError):
        raise HTTPException(400, "doc_id fehlt") from None
    job_id = await _proc(request).enqueue(inst.id, doc_id, "webhook", force=True)
    return JSONResponse({"queued": job_id is not None, "job_id": job_id})


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


def main() -> None:
    logging.basicConfig(level=os.environ.get("PJ_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        "paperless_jev.app:app",
        host=os.environ.get("PJ_HOST", "0.0.0.0"),
        port=int(os.environ.get("PJ_PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
