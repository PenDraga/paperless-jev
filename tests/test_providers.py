"""Cloudflare Workers AI als Klassifizierer (nach dem Vorschlag in #1)."""

from __future__ import annotations

import json

import httpx
import pytest

from paperless_jev.jev import JevError, classifier, classifier_missing

from .test_processor import client  # noqa: F401

QUESTIONS = {"tag:17": {"type": "noul", "instructions": "Server hosting?"}}
ANSWER = {"model": "jev-1.13.0", "answers": {"tag:17": {"type": "noul", "noul": 0.97}}, "usage": {"input_tokens": 123}}


def cloudflare_cfg(**extra):
    return {"classifier": "cloudflare", "cloudflare_account_id": "acct", "cloudflare_api_token": "cf-token",
            "typesafe_api_key": "", "model": "jev-latest", **extra}


async def ask(cfg, handler):
    jev, model = classifier(cfg)
    jev._http._transport = httpx.MockTransport(handler)
    async with jev:
        return await jev.ask("Monthly server invoice", QUESTIONS, model)


@pytest.fixture(autouse=True)
def no_cloudflare_env(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN_FILE", raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_cloudflare_request_and_unwrapped_result(wrapped):
    def handler(request):
        assert str(request.url) == "https://api.cloudflare.com/client/v4/accounts/acct/ai/run"
        assert request.headers["Authorization"] == "Bearer cf-token"
        assert json.loads(request.content) == {
            "model": "typesafe/jev", "input": {"state": "Monthly server invoice", "questions": QUESTIONS},
        }
        # so antwortet Workers AI tatsächlich: Hülle mit state "Completed"
        body = {"success": True, "errors": [], "result": {"state": "Completed", "result": ANSWER}} if wrapped else ANSWER
        return httpx.Response(200, json=body)

    assert classifier_missing(cloudflare_cfg()) is None
    assert await ask(cloudflare_cfg(), handler) == ANSWER


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"success": False, "errors": [{"message": "provider failure"}]},
    {"success": True, "result": {"state": "Queued"}},
    {"answers": {}},
    {"answers": {"tag:17": {"type": "noul", "noul": 1.1}}},
])
async def test_incomplete_cloudflare_result_fails(body):
    with pytest.raises(JevError):
        await ask(cloudflare_cfg(), lambda request: httpx.Response(200, json=body))


@pytest.mark.asyncio
async def test_insufficient_balance_is_reported():
    body = {"success": False, "errors": [{"message": "Insufficient balance; add money to your gateway or use BYOK", "code": 2021}]}
    with pytest.raises(JevError, match="Insufficient balance"):
        await ask(cloudflare_cfg(), lambda request: httpx.Response(402, json=body))


@pytest.mark.asyncio
async def test_cloudflare_environment_fallback(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "env-acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN_FILE", str(token_file))
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers["Authorization"]))
        return httpx.Response(200, json=ANSWER)

    await ask(cloudflare_cfg(cloudflare_account_id="", cloudflare_api_token=""), handler)
    await ask(cloudflare_cfg(), handler)
    assert seen == [
        ("/client/v4/accounts/env-acct/ai/run", "Bearer file-token"),
        ("/client/v4/accounts/acct/ai/run", "Bearer cf-token"),
    ]


def test_cloudflare_missing_credentials():
    assert classifier_missing(cloudflare_cfg(cloudflare_account_id="")) == "Keine Cloudflare-Konto-ID konfiguriert"
    assert classifier_missing(cloudflare_cfg(cloudflare_api_token="")) == "Kein Cloudflare-API-Token konfiguriert"


def test_setup_saves_cloudflare_and_keeps_token(client):  # noqa: F811
    form = {"classifier_kind": "cloudflare", "cloudflare_account_id": " acct ", "cloudflare_api_token": "cf-secret"}
    client.post("/setup/typesafe", data=form)
    client.post("/setup/typesafe", data={**form, "cloudflare_api_token": ""})
    cfg = client.app.state.config.all()
    assert (cfg["classifier"], cfg["cloudflare_account_id"], cfg["cloudflare_api_token"]) == ("cloudflare", "acct", "cf-secret")
    stored = {r["key"]: r["value"] for r in client.app.state.db.query("SELECT key, value FROM settings")}
    assert "cf-secret" not in stored["cloudflare_api_token"]
    page = client.get("/setup").text
    assert 'value="cloudflare" checked' in page and "cf-secret" not in page


def test_dashboard_warning_for_cloudflare(client):  # noqa: F811
    cfg = client.app.state.config
    cfg.update({"classifier": "cloudflare", "cloudflare_account_id": "acct", "typesafe_api_key": ""})
    assert "Kein Cloudflare-API-Token konfiguriert" in client.get("/").text
    cfg.update({"cloudflare_api_token": "tok"})
    assert 'class="flash bad"' not in client.get("/").text
