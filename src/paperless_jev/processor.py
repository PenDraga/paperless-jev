"""Warteschlange, Polling und die eigentliche Verarbeitung eines Dokuments."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .classifier import build_request, excluded_tags, interpret, plan, render_title
from .config import Config, Instance
from .db import Database
from .jev import JevClient, JevError
from .paperless import Metadata, PaperlessClient, PaperlessError, collect_examples
from .titles import TitleError, generate_title

log = logging.getLogger("paperless_jev")

ACTIVE = ("queued", "running")
META_TTL = 300
EXAMPLES_TTL = 3600
MAX_ERRORS = 3
WORKERS = 2


class Processor:
    def __init__(self, db: Database, config: Config) -> None:
        self.db = db
        self.config = config
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._meta: dict[int, tuple[float, Metadata]] = {}
        self._examples: dict[int, tuple[float, dict]] = {}
        self._tasks: list[asyncio.Task] = []

    # --- Lebenszyklus -----------------------------------------------------

    async def start(self) -> None:
        self.db.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'")
        for row in self.db.query("SELECT id FROM jobs WHERE status = 'queued' ORDER BY id"):
            self.queue.put_nowait(row["id"])
        self._tasks = [asyncio.create_task(self._worker()) for _ in range(WORKERS)]
        self._tasks.append(asyncio.create_task(self._poller()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def client(self, inst: Instance) -> PaperlessClient:
        return PaperlessClient(inst.url, inst.token, host=inst.host_header)

    async def metadata(self, inst: Instance, pl: PaperlessClient, fresh: bool = False) -> Metadata:
        cached = self._meta.get(inst.id)
        if cached and not fresh and time.monotonic() - cached[0] < META_TTL:
            return cached[1]
        meta = await pl.metadata()
        self._meta[inst.id] = (time.monotonic(), meta)
        return meta

    async def examples(
        self, inst: Instance, pl: PaperlessClient, meta: Metadata, cfg: dict[str, Any]
    ) -> dict[tuple[str, int], list[str]]:
        limit = int(cfg["examples"])
        if limit <= 0:
            return {}
        cached = self._examples.get(inst.id)
        if cached and time.monotonic() - cached[0] < EXAMPLES_TTL:
            return cached[1]
        kinds = ["document_type"]
        if cfg["fields"]["storage_path"]["enabled"]:
            kinds.append("storage_path")
        if cfg["fields"]["tags"]["enabled"]:
            kinds.append("tag")
        examples = await collect_examples(pl, meta, kinds, limit, excluded_tags(meta, cfg))
        self._examples[inst.id] = (time.monotonic(), examples)
        return examples

    def forget_metadata(self, instance_id: int | None = None) -> None:
        if instance_id is None:
            self._meta.clear()
            self._examples.clear()
        else:
            self._meta.pop(instance_id, None)
            self._examples.pop(instance_id, None)

    # --- Einreihen ------------------------------------------------------

    async def enqueue(self, instance_id: int, doc_id: int, source: str, force: bool = False) -> int | None:
        last = self.db.one(
            "SELECT id, status FROM jobs WHERE instance_id = ? AND doc_id = ? ORDER BY id DESC LIMIT 1",
            (instance_id, doc_id),
        )
        if last and last["status"] in ACTIVE:
            return None
        if last and not force:
            errors = self.db.one(
                "SELECT COUNT(*) AS n FROM jobs WHERE instance_id = ? AND doc_id = ? AND status = 'error'",
                (instance_id, doc_id),
            )["n"]
            if last["status"] != "error" or errors >= MAX_ERRORS:
                return None
        job_id = self.db.create_job(instance_id, doc_id, source)
        await self.queue.put(job_id)
        return job_id

    async def poll_once(self, instance_id: int | None = None, force: bool = False) -> int:
        count = 0
        for inst in self.config.instances(enabled_only=instance_id is None):
            if instance_id is not None and inst.id != instance_id:
                continue
            try:
                async with self.client(inst) as pl:
                    doc_ids = await pl.inbox_document_ids()
            except PaperlessError as e:
                log.warning("Polling %s fehlgeschlagen: %s", inst.name, e)
                continue
            for doc_id in doc_ids:
                if await self.enqueue(inst.id, doc_id, "manual" if force else "poll", force=force):
                    count += 1
        return count

    async def _poller(self) -> None:
        while True:
            minutes = int(self.config.all()["poll_minutes"])
            if minutes > 0 and self.config.all()["typesafe_api_key"]:
                try:
                    queued = await self.poll_once()
                    if queued:
                        log.info("Polling: %d Dokument(e) eingereiht", queued)
                except Exception:
                    log.exception("Polling fehlgeschlagen")
            await asyncio.sleep(max(minutes, 1) * 60)

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self.process(job_id)
            except Exception as e:  # der Worker darf nie sterben
                log.exception("Job %s fehlgeschlagen", job_id)
                self.db.update_job(job_id, status="error", error=str(e))
            finally:
                self.queue.task_done()

    # --- Verarbeitung ---------------------------------------------------

    async def process(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if not job:
            return
        inst = self.config.instance(job["instance_id"])
        if not inst:
            self.db.update_job(job_id, status="error", error="Instanz existiert nicht mehr")
            return
        cfg = self.config.all()
        if not cfg["typesafe_api_key"]:
            self.db.update_job(job_id, status="error", error="Kein TypeSafe-API-Key konfiguriert")
            return
        self.db.update_job(job_id, status="running", error=None)

        try:
            async with self.client(inst) as pl:
                doc = await pl.document(job["doc_id"])
                self.db.update_job(job_id, doc_title=doc.get("title"))
                meta = await self.metadata(inst, pl)
                ignore = meta.tag_id(cfg["tag_ignore"])
                if ignore is not None and ignore in doc.get("tags", []):
                    self.db.update_job(job_id, status="skipped", error="Tag zum Ignorieren gesetzt")
                    return

                similar: list[dict[str, Any]] = []
                if int(cfg["similar_docs"]) > 0:
                    try:
                        similar = await pl.similar_documents(doc["id"], int(cfg["similar_docs"]))
                    except PaperlessError as e:
                        log.info("Ähnliche Dokumente nicht verfügbar: %s", e)

                examples = await self.examples(inst, pl, meta, cfg)
                req = build_request(doc, meta, self.config.descriptions(inst.id), cfg, similar, examples)
                if not req.questions:
                    self.db.update_job(job_id, status="skipped", error="Keine Fragen - alle Felder deaktiviert?")
                    return

                async with JevClient(cfg["typesafe_api_key"]) as jev:
                    response = await jev.ask(req.state, req.questions, cfg["model"])

                result = interpret(response, req, meta, cfg)
                decision = plan(result, doc, cfg)
                result["plan"] = decision
                result["question_count"] = len(req.questions)
                result["current"] = {
                    k: doc.get(k) for k in ("correspondent", "document_type", "storage_path", "created", "tags")
                }
                self.db.update_job(
                    job_id,
                    result=result,
                    model=result["model"],
                    input_tokens=result["usage"].get("input_tokens"),
                )

                mode = cfg["mode"]
                if mode == "dry_run":
                    self.db.update_job(job_id, status="dry_run")
                elif mode == "review" or decision["needs_review"]:
                    updates = decision["updates"] if mode == "auto" else {}
                    applied = await self.apply(inst, pl, meta, doc, updates, cfg, finished=False)
                    self.db.update_job(job_id, status="review", applied=applied)
                else:
                    applied = await self.apply(inst, pl, meta, doc, decision["updates"], cfg, finished=True)
                    self.db.update_job(job_id, status="done", applied=applied)
        except (PaperlessError, JevError) as e:
            self.db.update_job(job_id, status="error", error=str(e))

    async def _tag(self, inst: Instance, pl: PaperlessClient, meta: Metadata, name: str) -> int:
        tid = meta.tag_id(name)
        if tid is None:
            tid = await pl.create_tag(name)
            meta.tags[tid] = name
            self._meta.pop(inst.id, None)
        return tid

    async def apply(
        self,
        inst: Instance,
        pl: PaperlessClient,
        meta: Metadata,
        doc: dict[str, Any],
        updates: dict[str, Any],
        cfg: dict[str, Any],
        finished: bool,
    ) -> dict[str, Any]:
        """Schreibt Werte nach Paperless.

        finished=True: Dokument ist fertig - Erledigt-Tag setzen, Review- und
        Posteingangs-Tags entfernen. Sonst Review-Tag setzen.
        """
        data = {k: v for k, v in updates.items() if k not in ("tags_add", "title")}
        tags = set(doc.get("tags", [])) | set(updates.get("tags_add", []))
        review_tag = await self._tag(inst, pl, meta, cfg["tag_review"])
        if finished:
            tags.discard(review_tag)
            tags.add(await self._tag(inst, pl, meta, cfg["tag_done"]))
            if cfg["remove_inbox"]:
                tags -= meta.inbox_tags
            title = updates.get("title") or await self.make_title(pl, meta, doc, {**doc, **data}, cfg)
            if title and title != doc.get("title"):
                data["title"] = title
        else:
            tags.add(review_tag)
        data["tags"] = sorted(tags)
        await pl.patch_document(doc["id"], data)
        return data

    async def make_title(
        self,
        pl: PaperlessClient,
        meta: Metadata,
        doc: dict[str, Any],
        merged: dict[str, Any],
        cfg: dict[str, Any],
    ) -> str | None:
        """Titel nach Einstellung; bei Fehlern bleibt der bisherige Titel."""
        values = {
            "document_type": meta.document_types.get(merged.get("document_type")),
            "correspondent": meta.correspondents.get(merged.get("correspondent")),
            "storage_path": meta.storage_paths.get(merged.get("storage_path")),
            "created": merged.get("created"),
            "title": doc.get("title"),
        }
        if cfg["title_mode"] == "template":
            return render_title(cfg["title_template"], values)
        if cfg["title_mode"] != "ollama" or not cfg["ollama_url"]:
            return None
        # Stilvorlage: Titel desselben Korrespondenten, sonst desselben Typs
        examples: list[str] = []
        for kind in ("correspondent", "document_type"):
            if merged.get(kind) and len(examples) < 8:
                try:
                    found = await pl.example_titles(kind, merged[kind], 8)
                except PaperlessError:
                    found = []
                examples += [t for t in found if t not in examples]
        facts = {
            "Absender": values["correspondent"],
            "Dokumenttyp": values["document_type"],
            "Datum": values["created"],
            "Dateiname": doc.get("original_file_name"),
        }
        try:
            return await generate_title(
                cfg["ollama_url"], cfg["ollama_model"], doc.get("content", ""), facts, examples
            )
        except TitleError as e:
            log.warning("Titel für Dokument %s nicht erzeugt: %s", doc.get("id"), e)
            return None

    async def apply_review(self, job_id: int, choice: dict[str, Any]) -> dict[str, Any]:
        """Übernimmt die in der Review-Queue bestätigten oder korrigierten Werte."""
        job = self.db.job(job_id)
        inst = self.config.instance(job["instance_id"]) if job else None
        if not job or not inst:
            raise PaperlessError("Job oder Instanz nicht gefunden")
        cfg = self.config.all()
        async with self.client(inst) as pl:
            doc = await pl.document(job["doc_id"])
            meta = await self.metadata(inst, pl)
            applied = await self.apply(inst, pl, meta, doc, choice, cfg, finished=True)
        suggested = (job.get("result") or {}).get("fields", {})
        corrections = {
            name: {"suggested": suggested.get(name, {}).get("value"), "chosen": choice.get(name)}
            for name in ("document_type", "correspondent", "storage_path", "created")
            if name in choice and suggested.get(name, {}).get("value") != choice.get(name)
        }
        self.db.update_job(
            job_id, status="done", source=f"{job['source']}+review",
            applied={**applied, "corrections": corrections},
        )
        return applied

    async def dismiss(self, job_id: int) -> None:
        job = self.db.job(job_id)
        inst = self.config.instance(job["instance_id"]) if job else None
        if job and inst:
            cfg = self.config.all()
            try:
                async with self.client(inst) as pl:
                    doc = await pl.document(job["doc_id"])
                    meta = await self.metadata(inst, pl)
                    review_tag = meta.tag_id(cfg["tag_review"])
                    if review_tag in doc.get("tags", []):
                        await pl.patch_document(doc["id"], {"tags": [t for t in doc["tags"] if t != review_tag]})
            except PaperlessError as e:
                log.warning("Review-Tag konnte nicht entfernt werden: %s", e)
        self.db.update_job(job_id, status="dismissed")
