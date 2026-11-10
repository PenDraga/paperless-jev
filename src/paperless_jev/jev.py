"""Client für den System-One-Endpunkt: TypeSafe Jev (direkt oder über Cloudflare Workers AI) oder lokal über Ollama (z. B. clef).

Bewusst direkt über HTTP statt über das SDK: der Endpunkt ist ein einzelner
POST, und so bleibt das Image klein und das Verhalten bei 429 nachvollziehbar.
Ollama nimmt dasselbe Format an (state, questions mit choice/noul), aber höchstens
64 Fragen pro Anfrage - grössere Anfragen werden aufgeteilt.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
CLOUDFLARE_URL = "https://api.cloudflare.com"
CLOUDFLARE_MODEL = "typesafe/jev"
# Unter Verbindungen wählbar: Jev (über das AI Gateway verrechnet) oder Cloudflares eigenes Clef
CLOUDFLARE_MODELS = {
    CLOUDFLARE_MODEL: "Jev (typesafe/jev)",
    "@cf/cloudflare/clef": "Clef 27B (@cf/cloudflare/clef)",
    "@cf/cloudflare/clef-flash": "Clef-flash 9B (@cf/cloudflare/clef-flash)",
}
SYSTEMONE_PATH = "/v1/systemone"
# Unter Setup wählbare Klassifizierer
CLASSIFIERS = ("typesafe", "cloudflare", "ollama")
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

# Preis laut docs.typesafe.ai/models (jev-1.13): 0.042 USD pro Mio. Input-Tokens
USD_PER_MTOK = 0.042
# Clef auf Workers AI laut developers.cloudflare.com/workers-ai/models (Stand 11/2026)
CLOUDFLARE_USD_PER_MTOK = {"@cf/cloudflare/clef": 0.24, "@cf/cloudflare/clef-flash": 0.038}


class JevError(Exception):
    pass


OLLAMA_MAX_QUESTIONS = 64
OLLAMA_MAX_CHOICES = 26  # clef: 2-26 Kandidaten pro Auswahlfrage
# Lokale Server, die viele Kandidaten ablehnen (z. B. clef über Ollama) - gemerkt pro URL.
# Andere (z. B. Decision-1.0-Lux, bis 255) werden ohne Turnier gefragt: das Turnier kostet dort Treffer.
_LIMITED: set[str] = set()


class TooManyCandidates(JevError):
    pass
NONE_LABEL = "(none of these)"  # wie classifier.NONE
ROUND_KEY = "{key}@{n}"


class JevClient:
    def __init__(
        self, api_key: str | None, timeout: float = 60.0, base_url: str | None = None,
        max_questions: int | None = None, keep_alive: str | None = None, max_choices: int | None = None,
        cloudflare_account: str | None = None, path: str = SYSTEMONE_PATH,
    ) -> None:
        # Workers AI: eigener Pfad und eine Hülle um dieselbe System-One-Antwort
        self.cloudflare_account = cloudflare_account
        if cloudflare_account:
            base_url = CLOUDFLARE_URL
        self._http = httpx.AsyncClient(
            base_url=(base_url or BASE_URL).rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )
        self.path = path  # nur für eigene Server; TypeSafe und Cloudflare haben feste Pfade
        self.max_questions = max_questions
        self.max_choices = max_choices
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.adaptive = False  # max_choices erst nach einer Ablehnung durch den Server
        self.keep_alive = keep_alive
        self.name = "Cloudflare" if cloudflare_account else "Ollama" if base_url else "TypeSafe"

    async def __aenter__(self) -> JevClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def ask(
        self, state: Any, questions: dict[str, dict[str, Any]], model: str
    ) -> dict[str, Any]:
        if self.adaptive and self.base_url not in _LIMITED:
            limit, self.max_choices = self.max_choices, None
            try:
                return await self._ask_tournament(state, questions, model)
            except TooManyCandidates:
                _LIMITED.add(self.base_url)
            finally:
                self.max_choices = limit
        return await self._ask_tournament(state, questions, model)

    async def _ask_tournament(
        self, state: Any, questions: dict[str, dict[str, Any]], model: str
    ) -> dict[str, Any]:
        big = {k: q for k, q in questions.items() if self._too_many(q)}
        if not big:
            return await self._ask_chunked(state, questions, model)
        # Turnier: Optionen in Gruppen bewerten, die besten ins Finale
        first = {k: q for k, q in questions.items() if k not in big}
        groups: dict[str, list[str]] = {}
        for key, q in big.items():
            labels = [lbl for lbl in q["criteria"] if lbl != NONE_LABEL]
            size = self.max_choices - 1
            for n, start in enumerate(range(0, len(labels), size)):
                part = labels[start:start + size]
                criteria = {lbl: q["criteria"][lbl] for lbl in part}
                criteria[NONE_LABEL] = q["criteria"].get(NONE_LABEL, "None of the listed options fits.")
                sub = ROUND_KEY.format(key=key, n=n)
                first[sub] = {**q, "criteria": criteria}
                groups.setdefault(key, []).append(sub)
        result = await self._ask_chunked(state, first, model)
        answers = result["answers"]
        final: dict[str, dict[str, Any]] = {}
        for key, subs in groups.items():
            scored: list[tuple[float, str]] = []
            for sub in subs:
                probs = (answers.pop(sub, None) or {}).get("probabilities", {})
                scored += [(p, lbl) for lbl, p in probs.items() if lbl != NONE_LABEL]
            best = [lbl for _p, lbl in sorted(scored, reverse=True)[: self.max_choices - 1]]
            q = big[key]
            criteria = {lbl: q["criteria"][lbl] for lbl in best if lbl in q["criteria"]}
            criteria[NONE_LABEL] = q["criteria"].get(NONE_LABEL, "None of the listed options fits.")
            if len(criteria) < 2:
                answers[key] = {"type": "choice", "choice": NONE_LABEL, "confidence": 0.0, "probabilities": {}}
                continue
            final[key] = {**q, "criteria": criteria}
        second = await self._ask_chunked(state, final, model)
        answers.update(second["answers"])
        result["usage"]["input_tokens"] += second["usage"]["input_tokens"]
        return result

    def _too_many(self, q: dict[str, Any]) -> bool:
        return bool(self.max_choices) and q.get("type") == "choice" and len(q.get("criteria") or {}) > self.max_choices

    async def _ask_chunked(
        self, state: Any, questions: dict[str, dict[str, Any]], model: str
    ) -> dict[str, Any]:
        if not questions:
            return {"answers": {}, "usage": {"input_tokens": 0}}
        if not self.max_questions or len(questions) <= self.max_questions:
            resp = await self._ask(state, questions, model)
            resp.setdefault("usage", {})
            resp["usage"]["input_tokens"] = int(resp["usage"].get("input_tokens") or 0)
            return resp
        # in Teilen fragen und die Antworten zusammenführen
        keys = list(questions)
        merged: dict[str, Any] = {"answers": {}, "usage": {"input_tokens": 0}}
        for i in range(0, len(keys), self.max_questions):
            part = await self._ask(state, {k: questions[k] for k in keys[i:i + self.max_questions]}, model)
            merged["model"] = part.get("model", model)
            merged["answers"].update(part.get("answers", {}))
            merged["usage"]["input_tokens"] += int(part.get("usage", {}).get("input_tokens") or 0)
        return merged

    async def _ask(self, state: Any, questions: dict[str, dict[str, Any]], model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"state": state, "model": model, "questions": questions}
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        path = self.path
        if self.cloudflare_account and model.startswith("@cf/"):
            # Cloudflares eigene Modelle: Modell im Pfad, System-One-Format ohne Hülle
            path = f"/client/v4/accounts/{self.cloudflare_account}/ai/run/{model}"
            payload = {"model": model.rsplit("/", 1)[-1], "state": state, "questions": questions}
        elif self.cloudflare_account:
            path = f"/client/v4/accounts/{self.cloudflare_account}/ai/run"
            payload = {"model": model, "input": {"state": state, "questions": questions}}
        delay = 1.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await self._http.post(path, json=payload)
            except httpx.HTTPError as e:
                if attempt == MAX_ATTEMPTS:
                    raise JevError(f"{self.name} nicht erreichbar: {e}") from e
            else:
                if resp.status_code < 400:
                    if not self.cloudflare_account:
                        return resp.json()
                    data = self._cloudflare_result(resp.json(), questions)
                    if model.startswith("@cf/"):
                        # Cloudflare meldet nur "clef" wie ein lokales Modell - fürs Protokoll und die Kosten den vollen Namen
                        data["model"] = model
                    return data
                if resp.status_code == 400 and "candidates" in resp.text:
                    raise TooManyCandidates(f"{self.name} HTTP 400: {resp.text[:500]}")
                if resp.status_code not in RETRY_STATUS or attempt == MAX_ATTEMPTS:
                    raise JevError(f"{self.name} HTTP {resp.status_code}: {resp.text[:500]}")
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    delay = float(retry_after)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
        raise JevError(f"{self.name}: keine Antwort")

    @staticmethod
    def _cloudflare_result(data: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Workers-AI-Hülle auspacken; unvollständige Antworten dürfen kein Dokument abschliessen."""
        if data.get("success") is False:
            raise JevError(f"Cloudflare: {data.get('errors')}")
        data = data.get("result", data)
        if data.get("state") == "Completed":
            data = data.get("result", {})
        answers = data.get("answers", {})
        if set(answers) != set(questions):
            raise JevError("Cloudflare: unvollständige Antwort")
        for key, question in questions.items():
            if question["type"] == "noul":
                value = answers[key].get("noul")
                if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise JevError("Cloudflare: ungültige Tag-Wahrscheinlichkeit")
        return data

    async def models(self) -> list[str]:
        if self.cloudflare_account:
            return [CLOUDFLARE_MODEL]
        resp = await self._http.get("/v1/models")
        if resp.status_code >= 400:
            raise JevError(f"TypeSafe HTTP {resp.status_code}: {resp.text[:300]}")
        return [m["name"] for m in resp.json().get("models", [])]


def classifier(cfg: dict[str, Any], timeout: float | None = None) -> tuple[JevClient, str]:
    """Client und Modellname je nach Einstellung unter Setup."""
    if cfg.get("classifier") == "ollama":
        # lokale Modelle sind langsamer, besonders beim ersten Laden
        client = JevClient(cfg.get("classifier_api_key") or None, timeout=timeout or 600.0, base_url=cfg["classifier_url"],
                           max_questions=OLLAMA_MAX_QUESTIONS, keep_alive="30m", max_choices=OLLAMA_MAX_CHOICES,
                           path=cfg.get("classifier_path") or SYSTEMONE_PATH)
        client.adaptive = True
        return client, cfg["classifier_model"]
    if cfg.get("classifier") == "cloudflare":
        account, token = cloudflare_credentials(cfg)
        model = cfg.get("cloudflare_model") or CLOUDFLARE_MODEL
        # Clef nimmt auch auf Cloudflare höchstens 64 Fragen pro Anfrage (aber beliebig viele Kandidaten)
        limit = OLLAMA_MAX_QUESTIONS if model.startswith("@cf/") else None
        return JevClient(token, timeout=timeout or 60.0, cloudflare_account=account, max_questions=limit), model
    return JevClient(cfg["typesafe_api_key"], timeout=timeout or 60.0), cfg["model"]


def clef_like(cfg: dict[str, Any]) -> bool:
    """Lokales Modell oder Clef auf Cloudflare: eigene Schwellwerte, Kriterien ohne Beispiele."""
    if cfg.get("classifier") == "cloudflare":
        return (cfg.get("cloudflare_model") or "").startswith("@cf/")
    return cfg.get("classifier") == "ollama"


def cloudflare_credentials(cfg: dict[str, Any]) -> tuple[str, str]:
    """Konto und Token aus Setup; sonst aus der Umgebung (Token als Datei, z. B. ein Docker-Secret)."""
    account = cfg.get("cloudflare_account_id") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = cfg.get("cloudflare_api_token") or ""
    if not token and (path := os.environ.get("CLOUDFLARE_API_TOKEN_FILE")):
        try:
            token = Path(path).read_text().strip()
        except OSError:
            token = ""
    return account.strip(), token


def classifier_missing(cfg: dict[str, Any]) -> str | None:
    """Grund, warum noch nicht klassifiziert werden kann (sonst None)."""
    if cfg.get("classifier") == "ollama":
        return None if cfg.get("classifier_url") and cfg.get("classifier_model") else "Ollama-URL oder Modell fehlt"
    if cfg.get("classifier") == "cloudflare":
        account, token = cloudflare_credentials(cfg)
        if not account:
            return "Keine Cloudflare-Konto-ID konfiguriert"
        return None if token else "Kein Cloudflare-API-Token konfiguriert"
    return None if cfg.get("typesafe_api_key") else "Kein TypeSafe-API-Key konfiguriert"
