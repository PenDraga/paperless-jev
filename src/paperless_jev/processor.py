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
from .llm import LLM, LLMError
from .titles import generate_title

log = logging.getLogger("paperless_jev")

ACTIVE = ("queued", "running")
TEST = "test"  # Quelle für Einzeltests: schreibt nie, zählt nicht fürs Polling
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
        # Status-Tags pro Instanz nur nacheinander anlegen (zwei Worker gleichzeitig -> "gibt es schon")
        self._tag_locks: dict[int, asyncio.Lock] = {}

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
        # Einzeltests zählen nicht: sonst würde das Polling ein getestetes Dokument überspringen
        last = self.db.one(
            "SELECT id, status FROM jobs WHERE instance_id = ? AND doc_id = ? AND source != ?"
            " ORDER BY id DESC LIMIT 1",
            (instance_id, doc_id, TEST),
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

    def _supersede(self, job: dict[str, Any]) -> None:
        """Ältere offene Review-Einträge desselben Dokuments durch den neuen Lauf ersetzen."""
        self.db.execute(
            "UPDATE jobs SET status = 'superseded', error = ? WHERE instance_id = ? AND doc_id = ?"
            " AND status = 'review' AND id < ?",
            (None, job["instance_id"], job["doc_id"], job["id"]),
        )

    async def recheck_reviews(self) -> int:
        """Alle Dokumente im Review mit der aktuellen Konfiguration neu klassifizieren."""
        count = 0
        for row in self.db.query("SELECT DISTINCT instance_id, doc_id FROM jobs WHERE status = 'review'"):
            if await self.enqueue(row["instance_id"], row["doc_id"], "manual", force=True):
                count += 1
        return count

    async def test_document(self, instance_id: int, doc_id: int) -> int:
        """Klassifiziert ein Dokument sofort als Probelauf und liefert die Job-ID."""
        job_id = self.db.create_job(instance_id, doc_id, TEST)
        try:
            await self.process(job_id)
        except Exception as e:
            log.exception("Test von Dokument %s fehlgeschlagen", doc_id)
            self.db.update_job(job_id, status="error", error=str(e))
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

                mode = "dry_run" if job["source"] == TEST else cfg["mode"]
                if mode == "dry_run":
                    self.db.update_job(job_id, status="dry_run")
                elif mode == "review" or decision["needs_review"]:
                    updates = decision["updates"] if mode == "auto" else {}
                    applied = await self.apply(inst, pl, meta, doc, updates, cfg, finished=False)
                    self.db.update_job(job_id, status="review", applied=applied)
                    self._supersede(job)
                else:
                    applied = await self.apply(inst, pl, meta, doc, decision["updates"], cfg, finished=True)
                    self.db.update_job(job_id, status="done", applied=applied)
                    self._supersede(job)
        except (PaperlessError, JevError) as e:
            self.db.update_job(job_id, status="error", error=str(e))

    async def _tag(self, inst: Instance, pl: PaperlessClient, meta: Metadata, name: str) -> int:
        tid = meta.tag_id(name)
        if tid is not None:
            return tid
        async with self._tag_locks.setdefault(inst.id, asyncio.Lock()):
            # inzwischen von einem anderen Job angelegt?
            cached = self._meta.get(inst.id)
            tid = meta.tag_id(name) or (cached[1].tag_id(name) if cached else None)
            if tid is None:
                try:
                    tid = await pl.create_tag(name)
                except PaperlessError:
                    # existiert schon (z. B. parallel angelegt): frisch laden und verwenden
                    tid = (await self.metadata(inst, pl, fresh=True)).tag_id(name)
                    if tid is None:
                        raise
            meta.tags[tid] = name
            if cached:
                cached[1].tags[tid] = name
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
        data = {k: v for k, v in updates.items() if k not in ("tags_add", "tags_remove", "title")}
        added = set(updates.get("tags_add", []))
        # Untertag gesetzt -> Obertags auch (wie in Paperless)
        added |= {a for tid in added for a in meta.tag_ancestors(tid)}
        tags = (set(doc.get("tags", [])) | added) - set(updates.get("tags_remove", []))
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
        strict: bool = False,
    ) -> str | None:
        """Titel nach Einstellung; bei Fehlern bleibt der bisherige Titel (strict: Fehler weitergeben)."""
        values = {
            "document_type": meta.document_types.get(merged.get("document_type")),
            "correspondent": meta.correspondents.get(merged.get("correspondent")),
            "storage_path": meta.storage_paths.get(merged.get("storage_path")),
            "created": merged.get("created"),
            "title": doc.get("title"),
        }
        if cfg["title_mode"] == "template":
            return render_title(cfg["title_template"], values)
        llm = LLM.from_config(cfg)
        if cfg["title_mode"] != "llm" or not llm:
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
            return await generate_title(llm, doc.get("content", ""), facts, examples)
        except LLMError as e:
            if strict:
                raise
            log.warning("Titel für Dokument %s nicht erzeugt: %s", doc.get("id"), e)
            return None

    async def suggest_title(self, job_id: int) -> str | None:
        """Titel, den paperless-jev beim Übernehmen erzeugen würde - schreibt nichts."""
        job = self.db.job(job_id)
        inst = self.config.instance(job["instance_id"]) if job else None
        if not job or not inst:
            raise PaperlessError("Job oder Instanz nicht gefunden")
        cfg = self.config.all()
        fields = (job.get("result") or {}).get("fields", {})
        async with self.client(inst) as pl:
            doc = await pl.document(job["doc_id"])
            meta = await self.metadata(inst, pl)
            suggested = {k: f["value"] for k, f in fields.items() if f.get("value") is not None}
            return await self.make_title(pl, meta, doc, {**doc, **suggested}, cfg, strict=True)

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
            job_id, status="done", source=f"{job['source']}+review", error=None,
            applied={**applied, "corrections": corrections},
        )
        return applied

    async def delete_document(self, job_id: int) -> None:
        """Dokument in Paperless löschen (Papierkorb); offene Einträge dazu als gelöscht markieren."""
        job = self.db.job(job_id)
        inst = self.config.instance(job["instance_id"]) if job else None
        if not job or not inst:
            raise PaperlessError("Job oder Instanz nicht gefunden")
        async with self.client(inst) as pl:
            await pl.delete_document(job["doc_id"])
        self.db.execute(
            "UPDATE jobs SET status = 'deleted' WHERE instance_id = ? AND doc_id = ?"
            " AND (id = ? OR status IN ('review', 'dry_run', 'error'))",
            (job["instance_id"], job["doc_id"], job_id),
        )

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
