# paperless-jev

[Deutsch](README.md) · **English**

Classifies new documents in the [Paperless-ngx](https://docs.paperless-ngx.com/) inbox with
[TypeSafe Jev](https://docs.typesafe.ai/introduction): document type, correspondent, storage path,
issue date and tags. Runs as a Docker container and is configured entirely through a web UI.

## Features

- **Classification with confidence** – Jev chooses from your existing Paperless metadata and returns a
  calibrated confidence for every answer. Confident values are applied, uncertain ones go to review.
- **Three modes** – *dry run* (log only), *suggestions only* (everything to review) and
  *automatic* (apply confident values, the rest to review).
- **Review queue** with thumbnail, document viewer, alternatives and one-click corrections.
- **Single test** – check one document by ID or Paperless link without writing anything.
- **Evaluation** on the overview: hit rate per field compared with your current filing.
- **Descriptions from keywords** – a local language model (Ollama or OpenAI-compatible, e.g. Qwen)
  turns your keywords into a full description in the interface language.
- **Titles** optionally by language model (in the style of documents already filed) or by template.
- **Filing conventions** – titles of documents already filed are sent to Jev as examples.
- **Webhook and polling**, multiple Paperless instances, log with cleanup.
- **Interface** in German and English, optimised for phones, automatic dark mode.

## How it works

```
Paperless-ngx ──webhook "document added"──▶ paperless-jev ──▶ TypeSafe Jev
      ▲              (or polling)               │
      └──────── REST: read document, set values ┘
```

Jev does not generate text, it **chooses**: from your metadata, or from dates that the code found in the
OCR text beforehand. Each document is sent to Jev as **one** request containing all questions.

| Confidence | Action (*automatic* mode) |
|---|---|
| ≥ auto threshold | value is set in Paperless directly |
| ≥ review threshold | suggestion in the review queue (tag `ai-review`) |
| below | ignored |

## Quick start

```bash
git clone https://github.com/PenDraga/paperless-jev.git
cd paperless-jev
cp .env.example .env
docker compose up -d --build
# → http://localhost:8090
```

1. **Setup** – enter your TypeSafe API key, add a Paperless instance with URL and API token, "Test connection".
2. **Rules** – start in *dry run* mode, choose fields and thresholds, optionally set up a language model.
3. **Descriptions** – add a few keywords for document types and tags, especially to separate similar
   categories ("… – not: …").
4. **Overview → Process inbox now** and check the log to see how good the suggestions are.
5. Then switch to *automatic* mode and create the webhook in Paperless (see below).

### Environment variables

Everything else is configured in the web UI. The environment only covers:

| Variable | Default | Purpose |
|---|---|---|
| `PJ_SECRET_KEY` | – | Key for encrypting stored tokens. If unset, `/data/secret.key` is created. |
| `PJ_ADMIN_USER` / `PJ_ADMIN_PASSWORD` | `admin` / – | Optional basic auth for the web UI (`/hook/*` and `/health` stay open). |
| `PJ_LOG_LEVEL` | `INFO` | Log level |
| `PJ_DATA_DIR` | `/data` | Data directory (SQLite, key) |

**Back up `/data`** – `paperless-jev.db` and `secret.key` belong together, otherwise the stored tokens can no longer be read.

### Running behind Traefik

Example: [`deploy/compose.yaml`](deploy/compose.yaml) – in the same network as Paperless, web UI reachable
from the internal network only (IP allowlist). Note: a headers middleware with `frameDeny: true`
(`X-Frame-Options: DENY`) blocks the document viewer. paperless-jev sends
`X-Frame-Options: SAMEORIGIN` itself, so a middleware without `frameDeny` is enough.

### Webhook in Paperless

Paperless (≥ 2.14) → *Workflows* → new workflow:

- Trigger *Document added*
- Action *Webhook*: `http://paperless-jev:8000/hook/<instance name>?token=<secret>` (secret on the setup page),
  *Use webhook parameters* enabled, parameter `doc_id` = `{{ doc_id }}`

Polling (every 5 minutes by default) stays active as a fallback and only processes inbox documents that
have never been evaluated.

## Privacy and cost

- The shortened OCR text and the names of your metadata are sent to `api.typesafe.ai`. According to its
  documentation, TypeSafe does not train on customer data. Documents tagged `ai-ignorieren` are never sent.
- The optional language model runs on your own hardware (Ollama, SGLang, vLLM, LM Studio …).
- jev-1.13 costs USD 0.042 per million input tokens. A document takes roughly 5–15k tokens, depending on
  the number of tags and examples, i.e. less than USD 0.001. The overview shows the total.

## Command-line dry run

Writes nothing to Paperless and shows suggestion, confidence and current value per document:

```bash
docker run --rm -it --network <paperless network> \
  -e PAPERLESS_URL=http://paperless:8000 -e PAPERLESS_TOKEN=... -e TYPESAFE_API_KEY=... \
  paperless-jev:latest paperless-jev-spike --sample 50 --blind
```

`--blind` hides the current filing and compares the suggestions with it. Descriptions can be tested
beforehand with `--descriptions`, template: [`beschreibungen.beispiel.json`](beschreibungen.beispiel.json).

## Development

```bash
pip install -e '.[dev]'
pytest
PJ_DATA_DIR=./data paperless-jev
```

Architecture, design decisions and measurements (German): [DOKUMENTATION.md](DOKUMENTATION.md).
