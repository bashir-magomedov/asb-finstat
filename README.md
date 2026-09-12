# ASB Finstat — team Friendly Strangers

Hackathon project. Single page: pick a country, type a company name, and an agent
finds that company's financial statement PDFs for the last 3 years, downloads them
locally, detects their language, translates non-English ones, and extracts the
financial statements — streaming every step live over a websocket.

Everything runs locally on one laptop. No hosting, no DB, no infra.

## The 5 AI calls

Every AI call goes through OpenRouter (`backend/app/openrouter.py`) and each one has
its own model, configurable via `backend/.env` (defaults in `backend/app/config.py`):

| # | Step             | File                                     | Model env var          | Owner  |
|---|------------------|------------------------------------------|------------------------|--------|
| 1 | Company typeahead| `backend/app/pipeline/company_search.py` | `MODEL_COMPANY_SEARCH` | —      |
| 2 | Find + download statement PDFs | `backend/app/pipeline/find_statements.py` | `MODEL_FIND_STATEMENTS` | — |
| 3 | Language check   | `backend/app/pipeline/language_check.py` (**stub**) | `MODEL_LANGUAGE_CHECK` | **Adel** |
| 4 | Translate to English | `backend/app/pipeline/translate.py` (**stub**) | `MODEL_TRANSLATE` | **Adel** |
| 5 | Extract financial statements | `backend/app/pipeline/extract.py` (**stub**) | `MODEL_EXTRACT` | **Sophie** |

Steps 2–5 are orchestrated by `backend/app/pipeline/runner.py`. Downloaded PDFs land
in `backend/data/downloads/<company-slug>/` (git-ignored).

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
{ "type": "search_companies", "country": "Germany", "query": "sie", "requestId": 3 }
{ "type": "run_pipeline", "country": "Germany", "company": { "name": "Siemens AG" } }
```

Server → client:

```jsonc
// typeahead results (echoes requestId so stale responses are dropped)
{ "type": "companies", "requestId": 3, "companies": [{ "name": "...", "description": "..." }] }

// live progress — one event per state change, rendered as the activity feed
{ "type": "step_update", "step": "find_statements", "status": "running", "message": "...", "data": null }
```

`step` is one of `pipeline | find_statements | language_check | translate | extract`;
`status` is `running | done | error | skipped`. The final event is
`step: "pipeline", status: "done"` with `data.results` containing per-file
`{year, file, language, statements}`.

## Plugging in your step (Adel / Sophie)

Your file is a stub with the exact contract in its docstring. In short:

- **Adel (#3)** — `language_check.py`: `async check_language(path, emit) -> dict`.
  Return `{"language": "...", "is_english": bool}`; the runner routes non-English
  files to #4, English ones straight to #5. pypdf + cryptography are installed for
  reading PDF text (Siemens' PDFs are AES-encrypted, cryptography handles that).
- **Adel (#4)** — `translate.py`: `async translate_pdf(path, language, emit) -> Path`.
  Take the non-English PDF, return the path to the English version. Use
  `settings.model_translate` with `app.openrouter.chat`/`chat_json`.
- **Sophie (#5)** — `extract.py`: `async extract_statements(path, emit) -> dict`.
  Take an English report, return the structured statements. Use `settings.model_extract`.

Call `await emit("<your-step>", "running"|"done"|"error", "human-readable message", optional_data_dict)`
as you go — it streams straight to the UI. No other files need to change.
