import httpx
import pytest

from paperless_jev import llm as llm_module
from paperless_jev.llm import LLM, complete, list_models


def _patch(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", factory)


async def test_openai_compatible_retries_without_template_kwargs(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        seen.append((str(request.url), "chat_template_kwargs" in body, request.headers.get("authorization")))
        if "chat_template_kwargs" in body:
            return httpx.Response(400, text="unknown field chat_template_kwargs")
        return httpx.Response(200, json={"choices": [{"message": {"content": "<think>x</think>Bern"}}]})

    _patch(monkeypatch, handler)
    out = await complete(LLM("openai", "http://sglang:8000/v1/", "m", "sk-1"), "?", max_tokens=5)
    assert out == "Bern"
    assert seen == [
        ("http://sglang:8000/v1/chat/completions", True, "Bearer sk-1"),
        ("http://sglang:8000/v1/chat/completions", False, "Bearer sk-1"),
    ]


async def test_ollama_and_model_lists(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"response": "Titel"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next"}]})
        return httpx.Response(404)

    _patch(monkeypatch, handler)
    assert await complete(LLM("ollama", "http://o:11434", "qwen3:8b"), "?", 5) == "Titel"
    assert await list_models(LLM("ollama", "http://o:11434", "x")) == ["qwen3:8b"]
    assert await list_models(LLM("openai", "http://s:8000", "x")) == ["qwen3.8-flash-next"]


def test_from_config_requires_url_and_model():
    assert LLM.from_config({"llm_url": "", "llm_model": "x"}) is None
    assert LLM.from_config({"llm_provider": "openai", "llm_url": "http://s", "llm_model": "m"}).provider == "openai"
