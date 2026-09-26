"""Web-UI, Webhook-Endpunkt und Start der Hintergrundverarbeitung."""

from __future__ import annotations

import base64
import html
import re
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
from . import i18n
from .i18n import gettext as _
from .jev import USD_PER_MTOK, JevClient, JevError
from .paperless import Metadata, PaperlessClient, PaperlessError
from .processor import TEST, Processor
from .llm import LLM, PROVIDERS, LLMError, list_models
from .titles import describe_category
from .vault import Vault

log = logging.getLogger("paperless_jev")
HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("PJ_DATA_DIR", "/data"))
ADMIN_USER = os.environ.get("PJ_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("PJ_ADMIN_PASSWORD", "")
PUBLIC_PATHS = ("/hook/", "/health", "/static/")
MODE_LABELS = {
    "dry_run": "Probelauf – schreibt nichts",
    "review": "Nur Vorschläge (Review)",
    "auto": "Automatisch + Review",
}

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
    FIELD_LABELS=FIELD_LABELS,
    STATUS_LABELS=STATUS_LABELS,
    MODE_LABELS=MODE_LABELS,
    RULE_LABELS={1: "beliebiges Wort", 2: "alle Wörter", 3: "exakt", 4: "Regex", 5: "ungefähr"},
    LANGUAGES=i18n.LANGUAGES,
    version=__version__,
    asset_v=int((HERE / "static" / "style.css").stat().st_mtime),
    _=_,
    current_lang=i18n.current.get,
)


@app.middleware("http")
async def frame_options(request: Request, call_next):
    """Nur die eigene Seite darf einbetten (Dokument-Viewer im Overlay), fremde Seiten nicht."""
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
    return response


@app.middleware("http")
async def language(request: Request, call_next):
    """Sprache für diesen Request: Cookie, sonst Browser-Einstellung."""
    i18n.current.set(i18n.pick(request.cookies.get(i18n.COOKIE), request.headers.get("accept-language")))
    return await call_next(request)


@app.get("/lang/{code}")
async def set_language(code: str, next: str = "/"):
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(target, status_code=303)
    if code in i18n.LANGUAGES:
        resp.set_cookie(i18n.COOKIE, code, max_age=365 * 24 * 3600, samesite="lax")
    return resp


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
    ctx.setdefault(
        "review_count",
        request.app.state.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'review'")["n"],
    )
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
        recent=await _jobs(request, limit=10),
        evaluation=await _evaluation(request),
    )


async def _metas(request: Request) -> dict[int, Metadata | None]:
    """Stammdaten je Instanz (gecacht), um IDs in Namen zu übersetzen."""
    metas: dict[int, Metadata | None] = {}
    for inst in _cfg(request).instances():
        try:
            async with _proc(request).client(inst) as pl:
                metas[inst.id] = await _proc(request).metadata(inst, pl)
        except PaperlessError:
            metas[inst.id] = None
    return metas


def _view(row: dict[str, Any], meta: Metadata | None) -> None:
    """Pro Feld: Vorschlag, Confidence, Stufe und Vergleich mit dem aktuellen Wert."""
    result = row.get("result") or {}
    current = result.get("current", {})
    view: dict[str, Any] = {}
    for name in SINGLE_FIELDS:
        f = result.get("fields", {}).get(name)
        cur = current.get(name)
        if name == "created":
            cur_label = cur
        else:
            cur_label = (meta.names(name).get(cur, f"#{cur}") if meta else f"#{cur}") if cur else None
        if not f:
            view[name] = {"label": None, "current": cur_label} if cur_label else None
            continue
        label = f["value"] if name == "created" and f["value"] else f["label"]
        view[name] = {
            "label": _("keiner passt") if f["value"] is None else label,
            "confidence": f["confidence"],
            "level": f["level"],
            "current": cur_label,
            "match": f["value"] is not None and f["value"] == cur,
        }
    row["view"] = view
    row["tags_view"] = [t for t in result.get("tags", []) if t["level"] != "low"]
    row["current_tags"] = [
        meta.tags.get(t, f"#{t}") for t in current.get("tags", []) if not meta or t not in meta.inbox_tags
    ] if current.get("tags") else []


async def _jobs(
    request: Request, status: str | None = None, limit: int = 100, q: str | None = None
) -> list[dict[str, Any]]:
    db: Database = request.app.state.db
    names = {i.id: i for i in _cfg(request).instances()}
    metas = await _metas(request)
    sql, where, params = "SELECT * FROM jobs", [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        where.append("(doc_title LIKE ? OR CAST(doc_id AS TEXT) = ?)")
        params += [f"%{q}%", q]
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = db.query(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
    for row in rows:
        row["instance"] = names.get(row["instance_id"])
        _view(row, metas.get(row["instance_id"]))
    return rows


async def _evaluation(request: Request) -> dict[str, Any] | None:
    """Auswertung über den jeweils letzten Lauf je Dokument."""
    db: Database = request.app.state.db
    rows = db.query(
        "SELECT * FROM jobs WHERE id IN (SELECT MAX(id) FROM jobs WHERE result IS NOT NULL"
        " GROUP BY instance_id, doc_id)"
    )
    if not rows:
        return None
    fields = {n: {"auto": 0, "match": 0, "compare": 0, "fill": 0, "suggest": 0, "none": 0} for n in SINGLE_FIELDS}
    tag_counts: dict[str, list[int]] = {}
    tag_docs = 0
    for row in rows:
        result = row["result"]
        current = result.get("current", {})
        for name, f in result.get("fields", {}).items():
            s = fields[name]
            cur = current.get(name)
            if f["level"] == "auto":
                s["auto"] += 1
                if cur:
                    s["compare"] += 1
                    s["match"] += f["value"] == cur
                else:
                    s["fill"] += 1
            elif f["level"] == "suggest":
                s["suggest"] += 1
            else:
                s["none"] += 1
        new_tags = [t for t in result.get("tags", []) if t["level"] != "low"]
        tag_docs += bool(new_tags)
        for t in new_tags:
            counts = tag_counts.setdefault(t["label"], [0, 0])
            counts[0 if t["level"] == "auto" else 1] += 1
    top_tags = sorted(tag_counts.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:12]
    return {"docs": len(rows), "fields": fields, "tag_docs": tag_docs, "top_tags": top_tags}


@app.get("/log", response_class=HTMLResponse)
async def job_log(request: Request, status: str | None = None, q: str | None = None, limit: int = 200):
    jobs = await _jobs(request, status or None, limit=min(limit, 2000), q=q or None)
    return _render(
        request, "log.html", jobs=jobs, status=status, q=q or "", limit=limit, instances=_cfg(request).instances(),
        cleanup=_cleanup_counts(request.app.state.db),
    )


@app.post("/run")
async def run_now(request: Request, instance_id: int = Form(0), force: bool = Form(False)):
    count = await _proc(request).poll_once(instance_id or None, force=force)
    return _redirect("/", msg=_("{n} Dokument(e) eingereiht", n=count))


def _parse_doc_id(raw: str) -> int | None:
    """Dokument-ID aus Zahl oder Paperless-Link (…/documents/2474/details)."""
    raw = raw.strip()
    if raw.lstrip("#").isdigit():
        return int(raw.lstrip("#"))
    match = re.search(r"/documents/(\d+)", raw)
    return int(match.group(1)) if match else None


@app.post("/test")
async def test_document(request: Request, doc: str = Form(""), instance_id: int = Form(0)):
    doc_id = _parse_doc_id(doc)
    instances = _cfg(request).instances()
    inst = next((i for i in instances if i.id == instance_id), instances[0] if instances else None)
    if doc_id is None or not inst:
        return _redirect("/", err=_("Bitte eine Dokument-ID oder einen Paperless-Link angeben"))
    job_id = await _proc(request).test_document(inst.id, doc_id)
    return _redirect(f"/jobs/{job_id}", msg=_("Test abgeschlossen – nichts wurde in Paperless geändert"))


@app.post("/jobs/{job_id}/test")
async def test_job(request: Request, job_id: int):
    job = request.app.state.db.job(job_id)
    if not job:
        raise HTTPException(404)
    new_id = await _proc(request).test_document(job["instance_id"], job["doc_id"])
    return _redirect(f"/jobs/{new_id}", msg=_("Test abgeschlossen – nichts wurde in Paperless geändert"))


@app.post("/jobs/{job_id}/title", response_class=HTMLResponse)
async def suggest_title(request: Request, job_id: int):
    """Titelvorschlag fürs Review-Formular (htmx) - schreibt nichts nach Paperless."""
    if _cfg(request).all()["title_mode"] == "off":
        return HTMLResponse(f'<span class="muted">{html.escape(_("Titel sind unter Regeln ausgeschaltet"))}</span>')
    try:
        title = await _proc(request).suggest_title(job_id)
    except (LLMError, PaperlessError) as e:
        return HTMLResponse(f'<span class="bad">{html.escape(str(e))}</span>')
    if not title:
        return HTMLResponse(f'<span class="bad">{html.escape(_("Kein Titel erzeugt – Sprachmodell unter Regeln prüfen"))}</span>')
    t = html.escape(title, quote=True)
    return HTMLResponse(
        f'<span class="title-suggestion"><strong>{t}</strong> '
        f'<button type="button" class="secondary outline" data-title="{t}" '
        f"""onclick="this.closest('form').querySelector('input[name=title]').value=this.dataset.title">"""
        f'{html.escape(_("ins Titelfeld"))}</button></span>'
    )


@app.post("/jobs/{job_id}/delete")
async def delete_job(request: Request, job_id: int):
    if not request.app.state.db.delete_jobs("id = ?", (job_id,)):
        return _redirect(f"/jobs/{job_id}", err=_("Laufende oder wartende Einträge lassen sich nicht löschen"))
    return _redirect("/log", msg=_("Eintrag #{id} gelöscht", id=job_id))


# Was sich im Protokoll bereinigen lässt: Schlüssel -> (Bedingung, Parameter)
CLEANUP = {
    "error": ("status = 'error'", ()),
    "test": ("source = ?", (TEST,)),
    "dismissed": ("status = 'dismissed'", ()),
    "skipped": ("status = 'skipped'", ()),
    "dry_run": ("status = 'dry_run' AND source != ?", (TEST,)),
}


def _cleanup_counts(db: Database) -> dict[str, int]:
    return {
        key: db.one(f"SELECT COUNT(*) AS n FROM jobs WHERE ({cond}) AND status NOT IN ('queued', 'running')", params)["n"]
        for key, (cond, params) in CLEANUP.items()
    }


@app.post("/log/cleanup")
async def cleanup_log(request: Request):
    form = await request.form()
    keys = [k for k in form.getlist("what") if k in CLEANUP]
    if not keys:
        return _redirect("/log", err=_("Nichts ausgewählt"))
    where = " OR ".join(f"({CLEANUP[k][0]})" for k in keys)
    params = [p for k in keys for p in CLEANUP[k][1]]
    n = request.app.state.db.delete_jobs(where, params)
    return _redirect("/log", msg=_("{n} Einträge gelöscht", n=n))


@app.post("/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: int):
    job = request.app.state.db.job(job_id)
    if not job:
        raise HTTPException(404)
    new_id = await _proc(request).enqueue(job["instance_id"], job["doc_id"], "manual", force=True)
    return _redirect(f"/jobs/{new_id}" if new_id else f"/jobs/{job_id}", msg=_("Neu eingereiht"))


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
    return {
        "meta": meta,
        "tag_options": {k: v for k, v in meta.tags.items() if k not in hidden},
        "title_mode": _cfg(request).all()["title_mode"],
    }


@app.get("/review", response_class=HTMLResponse)
async def review_queue(request: Request):
    items = []
    for job in await _jobs(request, "review", limit=50):
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
        return _redirect(str(form.get("back") or "/review"), msg=_("Verworfen"))
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
    if title := str(form.get("title") or "").strip():
        choice["title"] = title[:128]
    try:
        await _proc(request).apply_review(job_id, choice)
    except PaperlessError as e:
        return _redirect(f"/jobs/{job_id}", err=str(e))
    return _redirect(str(form.get("back") or "/review"), msg=_("Übernommen"))


@app.get("/doc/{instance_id}/{doc_id}")
async def document_preview(request: Request, instance_id: int, doc_id: int):
    """Dokument aus Paperless durchreichen - ohne separate Paperless-Anmeldung."""
    inst = _cfg(request).instance(instance_id)
    if not inst:
        raise HTTPException(404)
    try:
        async with _proc(request).client(inst) as pl:
            content, ctype = await pl.preview(doc_id)
    except PaperlessError:
        raise HTTPException(404) from None
    return Response(
        content,
        media_type=ctype,
        headers={"Content-Disposition": f'inline; filename="document-{doc_id}.pdf"', "Cache-Control": "private, max-age=600"},
    )


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
    return _redirect("/setup", msg=_("TypeSafe-Einstellungen gespeichert"))


@app.post("/setup/typesafe/test", response_class=HTMLResponse)
async def test_typesafe(request: Request):
    cfg = _cfg(request).all()
    if not cfg["typesafe_api_key"]:
        return HTMLResponse(f'<span class="bad">{_("Kein API-Key gespeichert")}</span>')
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
        '<span class="good">'
        + _('OK - {model} antwortet "{choice}" (Confidence {confidence})',
            model=resp.get("model"), choice=ans["choice"], confidence=f'{ans["confidence"]:.2f}')
        + "</span>"
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
        return _redirect("/setup", err=_("Für eine neue Instanz wird ein Token benötigt"))
    _cfg(request).save_instance(instance_id or None, name, url.strip(), public_url.strip(), token.strip(), enabled)
    _proc(request).forget_metadata(instance_id or None)
    return _redirect("/setup", msg=_("Instanz {name} gespeichert", name=name))


@app.post("/setup/instances/{instance_id}/delete")
async def delete_instance(request: Request, instance_id: int):
    _cfg(request).delete_instance(instance_id)
    return _redirect("/setup", msg=_("Instanz gelöscht"))


@app.post("/setup/instances/{instance_id}/test", response_class=HTMLResponse)
async def test_instance(request: Request, instance_id: int):
    inst = _cfg(request).instance(instance_id)
    if not inst:
        return HTMLResponse(f'<span class="bad">{_("Unbekannte Instanz")}</span>')
    try:
        async with PaperlessClient(inst.url, inst.token, timeout=10, host=inst.host_header) as pl:
            inbox = await pl.ping()
            meta = await pl.metadata()
    except PaperlessError as e:
        return HTMLResponse(f'<span class="bad">{e}</span>')
    return HTMLResponse(
        '<span class="good">'
        + _("OK - {inbox} im Posteingang, {types} Typen, {correspondents} Korrespondenten, {tags} Tags",
            inbox=inbox, types=len(meta.document_types), correspondents=len(meta.correspondents), tags=len(meta.tags))
        + "</span>"
    )


# --- Regeln ----------------------------------------------------------------


@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request):
    return _render(request, "rules.html", cfg=_cfg(request).all(), providers=PROVIDERS)


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
            "correspondent_fallback": str(form.get("correspondent_fallback") or "").strip(),
            "overwrite": "overwrite" in form,
            "review_conflicts": "review_conflicts" in form,
            "paperless_rules": "paperless_rules" in form,
            "remove_inbox": "remove_inbox" in form,
            "tag_done": str(form.get("tag_done") or cfg["tag_done"]).strip(),
            "tag_review": str(form.get("tag_review") or cfg["tag_review"]).strip(),
            "tag_ignore": str(form.get("tag_ignore") or cfg["tag_ignore"]).strip(),
            "title_mode": mode_title if (mode_title := str(form.get("title_mode", "off"))) in ("off", "template", "llm") else "off",
            "title_template": str(form.get("title_template") or cfg["title_template"]),
            "llm_provider": provider if (provider := str(form.get("llm_provider", "ollama"))) in PROVIDERS else "ollama",
            "llm_url": str(form.get("llm_url") or "").strip().rstrip("/"),
            "llm_model": str(form.get("llm_model") or "").strip(),
            **({"llm_api_key": key} if (key := str(form.get("llm_api_key") or "").strip()) else {}),
            "fields": fields,
        }
    )
    _proc(request).forget_metadata()
    return _redirect("/rules", msg=_("Regeln gespeichert"))


@app.post("/rules/llm/test", response_class=HTMLResponse)
async def test_llm(request: Request):
    llm = LLM.from_config(_cfg(request).all())
    if not llm:
        return HTMLResponse(f'<span class="bad">{_("URL und Modell speichern, dann testen")}</span>')
    try:
        models = await list_models(llm)
    except LLMError as e:
        return HTMLResponse(f'<span class="bad">{e}</span>')
    if llm.model not in models:
        return HTMLResponse(
            f'<span class="bad">{_("Modell {model} fehlt. Vorhanden: {available}", model=llm.model, available=", ".join(models) or "–")}</span>'
        )
    return HTMLResponse(f'<span class="good">{_("OK - {model} verfügbar", model=llm.label)}</span>')


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
                d = stored.get((kind, oid), {"text": "", "source": "", "active": True})
                rows.append({"id": oid, "name": name, "rule": meta.rules.get((kind, oid)), **d})
        except PaperlessError as e:
            error = str(e)
    return _render(
        request, "descriptions.html",
        instances=instances, inst=inst, kind=kind, kinds=KINDS, rows=rows, meta_error=error,
        can_translate=LLM.from_config(_cfg(request).all()) is not None,
    )


@app.post("/descriptions")
async def save_descriptions(request: Request):
    form = await request.form()
    instance_id = int(str(form["instance_id"]))
    kind = str(form["kind"])
    cfg = _cfg(request).all()
    stored = _cfg(request).descriptions(instance_id)
    llm = LLM.from_config(cfg)
    expand = llm is not None and "expand" in form
    expanded, failed = 0, []
    inst = _cfg(request).instance(instance_id)
    for oid_raw in form.getlist("ids"):
        oid = int(oid_raw)
        source = str(form.get(f"text_{oid}", "")).strip()
        active = f"active_{oid}" in form
        old = stored.get((kind, oid), {"text": "", "source": ""})
        text = old["text"]
        if source != old["source"]:
            # Nur geänderte Einträge ausformulieren
            text = source
            if source and expand and inst:
                name = str(form.get(f"name_{oid}", ""))
                try:
                    async with _proc(request).client(inst) as pl:
                        examples = await pl.example_titles(kind, oid, 8)
                except PaperlessError:
                    examples = []
                try:
                    text = await describe_category(llm, kind, name, source, examples, lang=i18n.current.get())
                    expanded += 1
                except LLMError:
                    failed.append(name or str(oid))
        _cfg(request).save_description(instance_id, kind, oid, text, active, source=source)
    msg = _("Beschreibungen gespeichert") + (_(", {n} vom Sprachmodell ausformuliert", n=expanded) if expanded else "")
    err = _("Sprachmodell fehlgeschlagen für: {names} – Eingabe wurde unverändert übernommen", names=", ".join(failed)) if failed else None
    return _redirect(f"/descriptions?instance_id={instance_id}&kind={kind}", msg=msg, err=err)


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
