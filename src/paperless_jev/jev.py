"""Client für den TypeSafe System-One-Endpunkt (Jev).

Bewusst direkt über HTTP statt über das SDK: der Endpunkt ist ein einzelner
POST, und so bleibt das Image klein und das Verhalten bei 429 nachvollziehbar.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

# Preis laut docs.typesafe.ai/models (jev-1.13): 0.042 USD pro Mio. Input-Tokens
USD_PER_MTOK = 0.042


class JevError(Exception):
    pass


class JevClient:
    def __init__(self, api_key: str, timeout: float = 60.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def __aenter__(self) -> JevClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def ask(
        self, state: Any, questions: dict[str, dict[str, Any]], model: str
    ) -> dict[str, Any]:
        payload = {"state": state, "model": model, "questions": questions}
        delay = 1.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await self._http.post("/v1/systemone", json=payload)
            except httpx.HTTPError as e:
                if attempt == MAX_ATTEMPTS:
                    raise JevError(f"TypeSafe nicht erreichbar: {e}") from e
            else:
                if resp.status_code < 400:
                    return resp.json()
                if resp.status_code not in RETRY_STATUS or attempt == MAX_ATTEMPTS:
                    raise JevError(f"TypeSafe HTTP {resp.status_code}: {resp.text[:500]}")
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    delay = float(retry_after)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
        raise JevError("TypeSafe: keine Antwort")

    async def models(self) -> list[str]:
        resp = await self._http.get("/v1/models")
        if resp.status_code >= 400:
            raise JevError(f"TypeSafe HTTP {resp.status_code}: {resp.text[:300]}")
        return [m["name"] for m in resp.json().get("models", [])]
