"""Schlanker async-Client für die Paperless-NGX-REST-API (API-Version 10)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

API_VERSION = "10"
PAGE_SIZE = 100


class PaperlessError(Exception):
    pass


@dataclass
class Metadata:
    """Stammdaten einer Instanz: id -> Name."""

    correspondents: dict[int, str] = field(default_factory=dict)
    document_types: dict[int, str] = field(default_factory=dict)
    storage_paths: dict[int, str] = field(default_factory=dict)
    tags: dict[int, str] = field(default_factory=dict)
    inbox_tags: set[int] = field(default_factory=set)

    def tag_id(self, name: str) -> int | None:
        wanted = name.strip().lower()
        for tag_id, tag_name in self.tags.items():
            if tag_name.lower() == wanted:
                return tag_id
        return None

    def names(self, kind: str) -> dict[int, str]:
        return {
            "correspondent": self.correspondents,
            "document_type": self.document_types,
            "storage_path": self.storage_paths,
            "tag": self.tags,
        }[kind]


class PaperlessClient:
    def __init__(self, url: str, token: str, timeout: float = 30.0, host: str | None = None) -> None:
        headers = {
            "Authorization": f"Token {token}",
            "Accept": f"application/json; version={API_VERSION}",
        }
        if host:
            # Paperless prüft ALLOWED_HOSTS: beim Zugriff über den internen
            # Containernamen muss der öffentliche Hostname mitgeschickt werden.
            headers["Host"] = host
        self._http = httpx.AsyncClient(base_url=url.rstrip("/"), headers=headers, timeout=timeout)

    async def __aenter__(self) -> PaperlessClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise PaperlessError(f"Paperless nicht erreichbar: {e}") from e
        if resp.status_code >= 400:
            raise PaperlessError(
                f"Paperless {method} {path}: HTTP {resp.status_code} {resp.text[:300]}"
            )
        return resp

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return (await self._request("GET", path, params=params)).json()

    async def _all(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        # Über die Seitennummer statt "next" paginieren: "next" enthält den
        # Host aus Sicht von Paperless, der von hier aus nicht erreichbar sein muss.
        results: list[dict] = []
        page = 1
        while True:
            data = await self._get(path, {**(params or {}), "page": page, "page_size": PAGE_SIZE})
            results.extend(data["results"])
            if not data.get("next"):
                return results
            page += 1

    async def ping(self) -> int:
        """Prüft Verbindung und Token; liefert die Anzahl Dokumente im Posteingang."""
        data = await self._get("/api/documents/", {"is_in_inbox": "true", "page_size": 1})
        return int(data["count"])

    async def document(self, doc_id: int) -> dict[str, Any]:
        return await self._get(f"/api/documents/{doc_id}/")

    async def inbox_document_ids(self) -> list[int]:
        docs = await self._all(
            "/api/documents/",
            {"is_in_inbox": "true", "fields": "id", "ordering": "added"},
        )
        return [d["id"] for d in docs]

    async def similar_documents(self, doc_id: int, limit: int) -> list[dict[str, Any]]:
        data = await self._get(
            "/api/documents/",
            {"more_like_id": doc_id, "page_size": limit},
        )
        return data["results"][:limit]

    async def metadata(self) -> Metadata:
        meta = Metadata()
        for c in await self._all("/api/correspondents/"):
            meta.correspondents[c["id"]] = c["name"]
        for t in await self._all("/api/document_types/"):
            meta.document_types[t["id"]] = t["name"]
        for s in await self._all("/api/storage_paths/"):
            meta.storage_paths[s["id"]] = s["name"]
        for t in await self._all("/api/tags/"):
            meta.tags[t["id"]] = t["name"]
            if t.get("is_inbox_tag"):
                meta.inbox_tags.add(t["id"])
        return meta

    async def example_titles(self, kind: str, object_id: int, limit: int) -> list[str]:
        """Titel bereits abgelegter Dokumente (nicht im Posteingang), ohne Duplikate."""
        param = "tags__id__all" if kind == "tag" else f"{kind}__id"
        data = await self._get(
            "/api/documents/",
            {param: object_id, "is_in_inbox": "false", "ordering": "-created",
             "fields": "title", "page_size": limit * 4},
        )
        titles: list[str] = []
        for d in data["results"]:
            title = " ".join((d.get("title") or "").split())
            if title and title not in titles:
                titles.append(title)
            if len(titles) >= limit:
                break
        return titles

    async def create_tag(self, name: str) -> int:
        # matching_algorithm 0 = keine automatische Zuordnung durch Paperless
        resp = await self._request(
            "POST", "/api/tags/", json={"name": name, "matching_algorithm": 0}
        )
        return int(resp.json()["id"])

    async def patch_document(self, doc_id: int, data: dict[str, Any]) -> dict[str, Any]:
        return (await self._request("PATCH", f"/api/documents/{doc_id}/", json=data)).json()

    async def preview(self, doc_id: int) -> tuple[bytes, str]:
        """Anzeigbare Fassung: das Archiv-PDF, sonst das Original (z. B. Bild)."""
        resp = await self._request("GET", f"/api/documents/{doc_id}/preview/")
        return resp.content, resp.headers.get("content-type", "application/pdf")

    async def thumbnail(self, doc_id: int) -> tuple[bytes, str]:
        resp = await self._request("GET", f"/api/documents/{doc_id}/thumb/")
        return resp.content, resp.headers.get("content-type", "image/webp")


async def collect_examples(
    pl: PaperlessClient, meta: Metadata, kinds: list[str], limit: int, skip_tags: set[int] | None = None
) -> dict[tuple[str, int], list[str]]:
    """Beispieltitel für alle Einträge der gewünschten Arten, parallel geladen."""
    sem = asyncio.Semaphore(6)
    targets = [
        (kind, oid)
        for kind in kinds
        for oid in meta.names(kind)
        if not (kind == "tag" and oid in (skip_tags or set()))
    ]

    async def fetch(kind: str, oid: int) -> list[str]:
        async with sem:
            try:
                return await pl.example_titles(kind, oid, limit)
            except PaperlessError:
                return []

    results = await asyncio.gather(*(fetch(k, o) for k, o in targets))
    return {t: r for t, r in zip(targets, results, strict=True) if r}
