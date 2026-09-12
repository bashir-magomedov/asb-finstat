# ASB Finstat — team Friendly Strangers

Hackathon project. Single page: pick a country, type a company name, and an agent
finds that company's financial statement PDFs for the last 3 years, downloads them
locally, detects their language, translates non-English ones, and extracts the
financial statements — streaming every step live over a websocket.

Everything runs locally on one laptop. No hosting, no DB, no infra.

## Company lookup

Company suggestions use **GLEIF → optional SEC EDGAR (US) → Wikidata → AI**.
The first source with usable records wins. Empty results, timeouts and provider
errors advance to the next source; AI remains the last resort.

- GLEIF and Wikidata require no accounts or API keys. GLEIF checks the selected
  legal entity's incorporation jurisdiction, including subdivisions such as
  `US-DE`. Its headquarters address is stored separately. This does not infer
  whether the entity is a group's ultimate parent; choose the intended entity.
- GLEIF returns active general entities with matching names/aliases. Funds,
  branches, inactive entities and duplicate/annulled/retired LEIs are excluded.
  A lapsed LEI is still searchable, with an overdue-renewal label.
- Wikidata supplies business-entity candidates with current country statements.
  These are labelled community data, with incorporation unverified. AI-only
  suggestions are explicitly labelled unverified and never receive invented IDs
  from a registry or source links.
- SEC is optional: set `SEC_USER_AGENT=ASB Finstat your-real-email@example.com`
  in `backend/.env` to identify requests under its fair-access policy. No SEC
  registration or API key is required. Its US adapter checks incorporation in
  a US state/DC, not the exchange listing or business address. Foreign filers
  and missing/unknown incorporation codes do not count as US matches.
- Companies House and OpenCorporates are not connected in this version.

Each suggestion retains its provider, identifier, source URL, country evidence
and retrieval time. The selected identity is passed to the financial-report
search. Finding a company does not guarantee public financial statements exist.

Searches are debounced and superseded requests are cancelled. Successful public
results are cached in memory for 10 minutes; AI/empty results for one minute.
Results following provider failures are not cached. Public sources have an
8-second deadline each; AI fallback has a 25-second deadline. Provider outages
are shown separately from an empty result. Try a longer name if a short prefix
has poor coverage; no source here is a complete worldwide company directory.

Implementation: `backend/app/company_sources.py` and
`backend/app/pipeline/company_search.py`. Source research:
[`docs/research/company-data-sources.md`](docs/research/company-data-sources.md).

## The 5 AI calls

Every AI call goes through OpenRouter (`backend/app/openrouter.py`) and each one has
its own model, configurable via `backend/.env` (defaults in `backend/app/config.py`):

| # | Step             | File                                     | Model env var          | Owner  |
|---|------------------|------------------------------------------|------------------------|--------|
| 1 | Company typeahead (last-resort fallback)| `backend/app/pipeline/company_search.py` | `MODEL_COMPANY_SEARCH` | —      |
| 2 | Find + download statement PDFs | `backend/app/pipeline/find_statements.py` | `MODEL_FIND_STATEMENTS` | — |
| 3 | Language check   | `backend/app/pipeline/language_check.py` | `MODEL_LANGUAGE_CHECK` | **Adel** |
| 4 | Translate to English | `backend/app/pipeline/translate.py` (**stub**) | `MODEL_TRANSLATE` | **Adel** |
| 5 | Locate and extract financial statements | `backend/app/pipeline/extract.py` | `MODEL_EXTRACT` | **Sophie** |

Steps 2–5 are orchestrated by `backend/app/pipeline/runner.py`. Downloaded PDFs land
in `backend/data/downloads/<company-slug>-<identity>/` (git-ignored).

## Financial statement extraction

Sophie's implementation lives in `backend/app/pipeline/statement_extractor.py`.
The existing `extract_statements(path, emit)` step calls it directly. Her sample
PDF and original sample outputs live in `backend/tests/fixtures/extraction/`.
The OpenRouter connection check is available from the backend directory with
`python -m app.check_openrouter`; it uses the application's shared client and configuration.

The extractor reads PDF text with PyMuPDF, locates statement pages, recovers source
tables, and writes JSON and Excel. The optional AI call only selects pages through
the existing OpenRouter client and `MODEL_EXTRACT`; numeric values come from the
document. If localization is unavailable, it uses heading heuristics and records a
review warning. Consolidated headings take priority over repeated narrative mentions.
Missing tables are explicitly listed; plain text evidence is retained separately.

Each extraction writes `report.json` and `report.xlsx` to a unique directory under
`backend/data/extractions/`. The activity view provides download links. Files are
served through `/artifacts/<id>/report.json` and `/artifacts/<id>/report.xlsx`;
the Vite development proxy forwards these requests to the backend.

Configuration in `backend/.env`:

- `EXTRACT_USE_LLM=true` enables AI page localization; set `false` for local-only extraction.
- `EXTRACT_OCR_MODE=auto` uses Tesseract when available for sparse image pages.
  `off` skips OCR; `required` fails if a scanned page needs OCR and Tesseract is missing.
- `EXTRACTION_DIR=data/extractions` controls the output directory.

Python dependencies are included in `backend/requirements.txt`. The Tesseract executable
must be installed separately and available on PATH for scanned-page OCR. Ordinary text
PDFs do not need it. The adapter also accepts UTF-8 `.txt` output from the translation
step, retaining warnings when columns are inferred from OCR or text spacing.

Translation is still Adel's stub; current statement-heading matching expects English.
Extraction output includes source pages, currency/scale, footnotes and warnings for
review rather than asserting accounting validation.

## Quickstart

Once set up (see below), just run `.\startapp.ps1` from the repo root — it starts
both servers in their own windows and skips anything already running.

### Backend (Python 3.13, FastAPI)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows; on mac/linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # then put your OpenRouter key in .env
uvicorn app.main:app --reload --port 8000
```

### Frontend (Vite + React + TS)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/ws` to the backend on port 8000.

## Websocket protocol (`/ws`)

Client → server:

```jsonc
{ "type": "search_companies", "country": "Germany", "countryCode": "de", "query": "sie", "requestId": 3 }
{ "type": "cancel_search" }
// Send the complete selected company object, including its identity/source fields.
{ "type": "run_pipeline", "country": "Germany", "company": { "name": "Siemens AG" } }
```

Server → client:

```jsonc
// typeahead results (echoes requestId so stale responses are dropped)
{ "type": "companies", "requestId": 3, "companies": [{ "id": "gleif:...", "name": "...", "source": "GLEIF", "verification": "source_record", "country_code": "DE", "jurisdiction": "DE", "source_url": "https://search.gleif.org/#/record/...", "retrieved_at": "..." }], "warnings": [] }

// live progress — one event per state change, rendered as the activity feed
{ "type": "step_update", "step": "find_statements", "status": "running", "message": "...", "data": null }
```

`step` is one of `pipeline | find_statements | language_check | translate | extract`;
`status` is `running | done | error | skipped`. The final event is
`step: "pipeline", status: "done"` with `data.results` containing per-file
`{year, file, language, statements}`.
The same final event retains the selected identity as `data.company`.

## Checks

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd ../frontend
npx tsc --noEmit
npm run build
```

Backend tests use mocked HTTP responses and do not require keys or spend AI credits.

## Pipeline step contracts

Translation (#4) remains a stub; language check and extraction are integrated. Contracts:

- **Adel (#3)** — `language_check.py`: `async check_language(path, emit) -> dict`.
  Returns `{"language": "...", "is_english": bool}`; the runner routes non-English
  files to #4, English ones straight to #5. A basic version is in place: pypdf reads
  the first 3 pages (cryptography handles Siemens' AES-encrypted PDFs) and
  `MODEL_LANGUAGE_CHECK` names the language. PDFs with no extractable text are
  reported and treated as English.
- **Adel (#4)** — `translate.py`: `async translate_pdf(path, language, emit) -> Path`.
  Take the non-English PDF, return the path to the English version. Use
  `settings.model_translate` with `app.openrouter.chat`/`chat_json`.
- **Sophie (#5)** — `extract.py`: `async extract_statements(path, emit) -> dict`.
  Takes an English PDF or text report. Returns `{source_pdf, statements,
  missing_statements, warnings, artifacts}`. Statement keys preserve her original
  `CashFlow`, `Balance Sheet`, and `IncomeStatement` schema. This payload appears
  in the extraction-done event and in each final pipeline result's `statements` field.

Call `await emit("<your-step>", "running"|"done"|"error", "human-readable message", optional_data_dict)`
as you go — it streams straight to the UI. No other files need to change.
