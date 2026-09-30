# paperless-jev documentation

[Deutsch](DOKUMENTATION.md) · **English**

## Architecture

```
Paperless-ngx ──webhook "document added"──▶ paperless-jev ──POST /v1/systemone──▶ TypeSafe (Jev)
      ▲                (or polling)              │
      └────────── REST: GET document, PATCH ─────┘
```

| Module | Purpose |
|---|---|
| `app.py` | FastAPI: web UI, `/hook/{instance}`, `/health`, starts background processing |
| `processor.py` | queue (asyncio), polling, processing, writing back to Paperless |
| `classifier.py` | builds the Jev request and interprets the answer (pure functions, tested) |
| `candidates.py` | finds dates and correspondent candidates in code |
| `llm.py` / `titles.py` | optional: local language model (Ollama or OpenAI-compatible, e.g. SGLang/vLLM) for titles and for descriptions from keywords |
| `paperless.py` / `jev.py` | thin HTTP clients (Paperless-ngx 2.x and 3.x, TypeSafe System One) |
| `config.py` / `db.py` / `vault.py` | settings, instances, descriptions, jobs in SQLite; tokens encrypted with Fernet |
| `static/review.js` / `static/scan.js` | review with pdf.js, scanner with OpenCV.js and jsPDF (in the browser) |

Everything persistent lives in `/data`: `paperless-jev.db` and – without `PJ_SECRET_KEY` – `secret.key`.
**Back up both files together**, otherwise the stored tokens can no longer be read.

## Processing a document

1. Load the document; documents tagged *ai-ignorieren* are skipped.
2. Load the instance's metadata (cached for 5 minutes).
3. Optionally, similar documents already filed (`more_like_id`) as context.
4. **One** Jev request with all questions:
   - `choice` document type, correspondent, storage path – options = Paperless metadata + "none of these";
     per option the description and (type, storage path) titles of documents already filed as examples
   - `choice` issue date – options = dates found by regex, with context
   - one `noul` per tag ("does tag X apply?") – for new tags and, as a **double-check**, for tags already set
5. Decision by mode:
   - **Dry run**: log only.
   - **Suggestions only**: set tag *ai-review*, review queue.
   - **Automatic**: apply confident values. If a field is still empty without a confident value → *ai-review*,
     otherwise *ai-klassifiziert* + remove the inbox tag. Uncertain tag suggestions alone do not trigger a review
     (switchable) – but they show up in the review if the document is checked anyway.
   - **Contradictions**: if Jev confidently disagrees with an existing value, the document goes to review; above the
     *replace existing value* threshold (e.g. 0.98) Jev corrects it itself. A tag already set that Jev confidently
     considers wrong also sends the document to review – nothing is ever removed automatically.
6. Values confirmed or corrected in the review are written; corrections are counted (overview) and help to
   fine-tune descriptions and thresholds.

## Review

One document at a time ("3 of 22"). The PDF is rendered with pdf.js right in the review; values chosen by Jev
(sender, type, date) are highlighted in the text layer – green confident, yellow uncertain. Fields can be changed
through selection sheets (Jev's suggestions with probability, current value, search), tags are chips; existing tags
Jev considers wrong are unselected in advance and removed when applying. After applying or dismissing, the next
document follows.

- **Re-check all** classifies all review documents again with the current configuration; a new run supersedes
  older review entries of the same document (status *superseded*).
- **Delete document** deletes it in Paperless (trash, restorable) and marks the entries as *deleted*.

## Design decisions

- **Jev only for decisions.** According to the [Jev 1.13 jaggedness notes](https://docs.typesafe.ai/model-jaggedness/jev-1.13),
  arithmetic, date comparison and counting are weak – so code finds date candidates and Jev only chooses.
- **English questions, descriptions in the interface language.** Jev is trained primarily on English, so the fixed
  questions are English. For descriptions a comparison showed no advantage for English (see *Measurements*), so they
  stay in the interface language.
- **Existing values are kept** ("overwrite" off), so Paperless' own matching and manual assignments take precedence –
  unless Jev disagrees very confidently (separate, configurable threshold, off by default). Exception: the date –
  Paperless always sets one, Jev corrects it only with high confidence.
- **Filing conventions through examples.** Jev reads literally: a "Gutschriftsanzeige" (credit advice) ends up as
  *Gutschrift* (credit note) without help, even if you file such documents as *Kontoauszug* (account statement). So
  every option gets the titles of up to 5 documents already filed (not in the inbox) as a structured criterion
  (`{"what": …, "titles_of_documents_already_filed_here": […]}`, see docs.typesafe.ai/primitives/advanced).
  The examples are cached for one hour.
- **Correspondent pre-filter** only above 255 entries (choice limit). An earlier pre-filter from 30 entries removed
  the correct correspondents in the first dry run.
- **Text truncation** to `max_chars` (70 % start, 30 % end): sender, date and amounts are almost always there; Jev
  allows 32k tokens for state + longest question.
- **No Redis/Celery.** An in-process queue is enough for inbox volumes; open jobs are re-queued from SQLite after a
  restart.
- **Pin the model**: `jev-latest` moves with releases; if you have calibrated thresholds, enter a fixed version
  (e.g. `jev-1.13.0`).

## Titles

Jev does not write text. Titles are therefore created either
- **by language model** (recommended, replaces paperless-gpt): after classification a local model writes a title –
  via Ollama (e.g. `qwen3:8b`) or an OpenAI-compatible API (e.g. SGLang); for Qwen3 the thinking mode is switched
  off. Up to 8 titles of documents already filed from the same correspondent or type serve as style examples.
  Years, dates and person names are avoided because Paperless stores them separately. About 1–2 s per document.
- **by template**, e.g. `{document_type} {correspondent} {created:%Y-%m}`,
- or not at all.

Answers from the language model are checked: questions back, explanations or Markdown are rejected and requested
once more; if a title is merely too long, a shorter version is requested (up to 100 characters accepted). Broken
titles in the archive (e.g. leftovers from other tools) are not used as style examples. *Suggest title* in the review
shows the title beforehand without writing anything.

A title entered in the review takes precedence. Paperless workflows triggered by *document updated* that set a title
run after paperless-jev and therefore have the last word.

## Descriptions from keywords

Under *Descriptions*, keywords are enough ("bank documents, credit, debit – not: tax certificate"). On save, the
language model turns changed entries into a description – in the interface language, with "Not: …" / "Nicht: …" for
exclusions – using the titles of documents already filed. Both versions are stored: the keywords (in the input
field) and the text for Jev (shown below with "→"). Invented exclusions are removed if the keywords contain no
"not/no/without". An entry is only rewritten when its keywords change.

The Paperless matching rules (search term and algorithm) are shown in grey next to each entry. Under *Rules* they can
additionally be passed to Jev as a hint (default: off, see *Measurements*).

## Log

By default the log shows only open entries (review, dry runs, errors, queued); filters by status, document type,
correspondent and tag, and a click on a value in the table filters directly. Times are shown in the `TZ` time zone
(default Europe/Zurich), stored in UTC.

Every run is a job in SQLite. The jobs are also the memory for polling: a document that has already been evaluated
is not classified again. *Clean up log* therefore only deletes errors, single tests, dismissed and skipped entries;
dry runs only with a warning. Running, queued and finished entries are always kept.

## Measurements

Blind tests on 60 documents already filed each (Jev does not see the current filing; results are compared with it).
Two identical runs differ by ±2 hits – smaller differences are chance.

| Question | Result |
|---|---|
| Descriptions in English or German? | Document type EN 44–46/60, DE 49/60, none 46/60. The German lead came almost entirely from 4 dividend documents that an English exclusion misdirected. Descriptions mainly reduce wrong tags ("tax-relevant": 8 instead of 14 false alarms). |
| Paperless matching rules as a hint? | Correspondent 52/59 with and without, tags the same (within the variation), but +40 % tokens. Therefore off by default. |
| Double-check existing tags? | 70 tags on 60 documents: 64 confirmed, 6 uncertain, 0 confidently wrong – no false alarms, about +20 % tokens. On by default. |

## Document viewer

On the desktop the preview opens the PDF in an overlay (iframe), on phones in a new tab. The app sends
`X-Frame-Options: SAMEORIGIN` and `frame-ancestors 'self'`. A reverse proxy must not additionally send
`X-Frame-Options: DENY` (Traefik: `frameDeny: true`), otherwise the browser reports "refused to connect". The viewer
appends `?view=…` so the browser does not reuse a cached PDF with old headers.

## Scanning

Under *Scan*, the phone captures documents right in the browser (camera over HTTPS only). OpenCV.js detects the
document edges live, straightens the page in perspective and offers the filters Original, Greyscale and "Scan"
(lighting evened out, high contrast). Several pages are combined into one PDF in the browser with jsPDF and sent via
paperless-jev to `POST /api/documents/post_document/`; the API token stays on the server. The page follows the
Paperless task and queues the new document for classification right away, without waiting for webhook or polling.
Ready-made PDFs (e.g. iOS "Scan Documents") can be uploaded on the same page. A `Permissions-Policy` header from the
reverse proxy must not block the camera (`camera=(self)`).

Libraries are loaded from a CDN at runtime, not bundled: OpenCV.js (`@techstark/opencv-js`, Apache 2.0, ~10 MB,
only when the camera starts), jsPDF (MIT), pdf.js (Apache 2.0, in the review).

## Webhook

Paperless (≥ 2.14) → *Workflows* → new workflow:

- Trigger *Document added*
- Action *Webhook*: URL `http://paperless-jev:8000/hook/<instance name>?token=<secret>` (secret on the setup page),
  *Use webhook parameters* enabled, parameter `doc_id` = `{{ doc_id }}`

The webhook queues the document immediately. Polling (every 5 minutes by default) stays active as a fallback and
only processes inbox documents that have never been evaluated (errors: up to 3 attempts).

## Privacy

The shortened OCR text and the names of the metadata are sent to `api.typesafe.ai`. According to its documentation,
TypeSafe does not train on customer data; zero data retention is only available in the Enterprise plan. Tag sensitive
documents with *ai-ignorieren* (e.g. via a Paperless workflow by storage path or correspondent).

## Cost

jev-1.13: USD 0.042 per million input tokens, output free. Measured: on average about 10k tokens per document (text
up to 12,000 characters, ~50 tag questions, example titles), i.e. about USD 0.0004. The overview shows the total.
