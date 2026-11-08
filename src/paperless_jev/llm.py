"""Generatives Sprachmodell für Titel und Beschreibungen.

Zwei Schnittstellen:
- ``ollama``: native Ollama-API (/api/generate)
- ``openai``: OpenAI-kompatible API (/v1/chat/completions) - SGLang, vLLM,
  LM Studio, LiteLLM, llama.cpp-Server oder OpenAI selbst
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

PROVIDERS = {"ollama": "Ollama", "openai": "OpenAI-kompatibel (SGLang, vLLM, LM Studio …)"}


class LLMError(Exception):
    def __init__(self, message: str, *, public_message: str = "Sprachmodell-Anfrage fehlgeschlagen"):
        super().__init__(message)
        # Nur kontrollierte Diagnosen, keine URLs, Provider-Antworten oder Zugangsdaten.
        self.public_message = public_message


@dataclass(frozen=True)
class LLM:
    provider: str
    url: str
    model: str
    api_key: str = ""

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> LLM | None:
        if not cfg.get("llm_url") or not cfg.get("llm_model"):
            return None
        return cls(cfg.get("llm_provider") or "ollama", cfg["llm_url"], cfg["llm_model"], cfg.get("llm_api_key") or "")

    @property
    def label(self) -> str:
        return f"{self.model} ({PROVIDERS.get(self.provider, self.provider).split(' ')[0]})"

    def _openai_base(self) -> str:
        base = self.url.rstrip("/")
        return base if base.endswith("/v1") else f"{base}/v1"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


# Denkt ein Modell trotzdem (Gateway ignoriert "Denkmodus aus"), reicht das knappe Limit nicht
REASONING_BUDGET = 1024


def strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    # abgeschnittenes Nachdenken ohne </think> ist keine Antwort
    return re.sub(r"^\s*<think>.*", "", text, flags=re.DOTALL).strip()


async def complete(
    llm: LLM, prompt: str, max_tokens: int, temperature: float = 0.2, timeout: float = 180.0
) -> str:
    """Ein Prompt, eine Antwort - ohne Denkmodus, wo das Modell ihn kennt.

    Hat ein Modell das Limit fürs Nachdenken verbraucht und nichts geantwortet,
    wird einmal mit grösserem Limit nachgefragt.
    """
    text, cut_off = await _complete_once(llm, prompt, max_tokens, temperature, timeout)
    if not text and cut_off:
        text, _ = await _complete_once(llm, prompt, max(REASONING_BUDGET, max_tokens * 8), temperature, timeout)
    return text


async def _complete_once(
    llm: LLM, prompt: str, max_tokens: int, temperature: float, timeout: float
) -> tuple[str, bool]:
    """Antworttext und ob das Token-Limit erreicht wurde."""
    async with httpx.AsyncClient(timeout=timeout, headers=llm._headers()) as http:
        try:
            if llm.provider == "openai":
                payload: dict[str, Any] = {
                    "model": llm.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    # Qwen3 & Co.: Denkmodus aus (SGLang/vLLM); andere Server lehnen das evtl. ab
                    "chat_template_kwargs": {"enable_thinking": False},
                }
                url = f"{llm._openai_base()}/chat/completions"
                resp = await http.post(url, json=payload)
                if resp.status_code == 400 and "chat_template_kwargs" in resp.text:
                    payload.pop("chat_template_kwargs")
                    resp = await http.post(url, json=payload)
            else:
                payload = {
                    "model": llm.model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": temperature, "num_ctx": 8192, "num_predict": max_tokens},
                }
                resp = await http.post(f"{llm.url.rstrip('/')}/api/generate", json=payload)
        except httpx.HTTPError as e:
            reason = "Zeitüberschreitung beim Sprachmodell" if isinstance(e, httpx.TimeoutException) else "Sprachmodell nicht erreichbar"
            raise LLMError(f"Sprachmodell nicht erreichbar ({llm.url}): {e}", public_message=reason) from e
    if resp.status_code >= 400:
        raise LLMError(f"Sprachmodell HTTP {resp.status_code}: {resp.text[:200]}", public_message=f"HTTP {resp.status_code}")
    data = resp.json()
    if llm.provider == "openai":
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        cut_off = choice.get("finish_reason") == "length"
    else:
        text = data.get("response", "")
        cut_off = data.get("done_reason") == "length"
    return strip_thinking(text), cut_off


async def list_models(llm: LLM) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=10, headers=llm._headers()) as http:
            if llm.provider == "openai":
                resp = await http.get(f"{llm._openai_base()}/models")
            else:
                resp = await http.get(f"{llm.url.rstrip('/')}/api/tags")
    except httpx.HTTPError as e:
        reason = "Zeitüberschreitung beim Sprachmodell" if isinstance(e, httpx.TimeoutException) else "Sprachmodell nicht erreichbar"
        raise LLMError(f"Sprachmodell nicht erreichbar ({llm.url}): {e}", public_message=reason) from e
    if resp.status_code >= 400:
        raise LLMError(f"Sprachmodell HTTP {resp.status_code}: {resp.text[:200]}", public_message=f"HTTP {resp.status_code}")
    data = resp.json()
    if llm.provider == "openai":
        return [m["id"] for m in data.get("data", [])]
    return [m["name"] for m in data.get("models", [])]
