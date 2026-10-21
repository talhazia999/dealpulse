# DealPulse

**An AI deal assistant for B2B CRMs.** Open any deal, click once, and DealPulse reads the deal's emails, notes and call logs, then returns:

- a short **summary** of where the deal stands
- **risk flags** (no reply, no decision-maker, stalled stage, passed close date, competitor, champion left, budget), each linked to the activity it came from
- the single best **next step**
- a ready-to-send **follow-up email**
- **CRM field updates** that are written back only after the rep approves them

This repository has three parts that share one Python backend:

- **API** (`app/`): FastAPI, Claude, and CRM connectors.
- **Demo page** (`web/`): served by the API at `/`. Visitors try it on sample deals.
- **HubSpot app** (`hubspot/`): a card on every HubSpot deal record. Each install is recorded as a lead.

## Design decisions

| Decision | Why |
|---|---|
| **Dates and counts are computed in code, not by the AI** (`app/signals.py`) | "No reply in 16 days" is always correct. The model reasons over exact facts instead of doing date math. |
| **One structured-output call per analysis** (`app/analyzer.py`) | The response is validated against a Pydantic schema, so the UI never receives malformed data. |
| **Every risk cites its evidence** | Reps can check where a flag came from. Evidence IDs that don't exist in the deal are removed before the response is returned. |
| **Approve before write** | The AI only suggests field updates, and only for `next_step`, `decision_maker` and `pain_points`. Nothing changes in the CRM until the rep approves. |
| **Deal content is treated as untrusted data** | Prospects write the emails, so the prompt tells the model to read activity text as information about the deal, never as instructions. |
| **No deal data is stored** | Deals are read from the CRM, analyzed and returned. |
| **CRM connectors behind one interface** (`app/crm/base.py`) | The analyzer never talks to a CRM directly. Adding HubSpot, Pipedrive or Salesforce means writing one connector class. |
| **AI evals alongside unit tests** (`tests/evals/`) | The real model runs on sample deals and is checked for the risks a sales manager would catch, including *not* over-flagging a healthy deal. |
| **Repeat analyses are free** | An unchanged deal returns the cached result, so clicking again or a busy public demo doesn't spend API credits. |
| **Every HubSpot request is verified** | The card's requests are signed by HubSpot (signature v3). The API checks the signature before it reads any data. |

## Architecture

```
Demo page (web/)                 HubSpot card (hubspot/)
        │  /api/...                      │  /hubspot/card/...  (signed by HubSpot)
        ▼                                ▼
FastAPI  app/main.py              app/hubspot_app.py  OAuth install · signature check
        │                                │
        └──────────── app/service.py ────┘   load deal · analyze · save approved fields
                          │
        ├── CRM connector  (app/crm/)          sample.py · hubspot.py
        ├── Signals        (app/signals.py)    exact dates and counts
        └── Analyzer       (app/analyzer.py)   Claude, structured output → validated result
```

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
# then put your Anthropic API key in .env

uvicorn app.main:app --reload --port 8010
```

- Demo page: http://localhost:8010
- Interactive API docs: http://localhost:8010/docs

## API

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/deals` | List the deals available from the connected CRM |
| `GET` | `/api/deals/{id}` | Get a deal with its contacts and activities |
| `GET` | `/api/deals/{id}/signals` | The exact dates and counts, without an AI call |
| `POST` | `/api/deals/{id}/analyze` | Analyze a deal from the CRM |
| `POST` | `/api/analyze` | Analyze a deal sent in the request body |
| `POST` | `/api/deals/{id}/apply-updates` | Write rep-approved field updates back to the CRM |
| `GET` | `/hubspot/install` | Start the HubSpot install (OAuth) |
| `GET` | `/hubspot/oauth/callback` | Finish the install, save the account, create DealPulse's deal properties |
| `POST` | `/hubspot/card/analyze` | Analyze a deal for the HubSpot card (signed requests only) |
| `POST` | `/hubspot/card/apply-updates` | Write approved fields from the HubSpot card (signed requests only) |

Try it:

```bash
curl -X POST http://localhost:8010/api/deals/northwind/analyze
```

## Sample deals

| Deal | Situation | What a good analysis catches |
|---|---|---|
| `northwind` | Proposal sent 16 days ago, no reply; CFO approval needed but CFO never contacted; competitor trial ending | No response, missing decision-maker, competitor |
| `brightline` | Healthy: decision-maker engaged, security review booked, budget approved | No high-severity risks (tests that it doesn't invent problems) |
| `kestrel` | Close date passed; the decision-maker left the company; budget frozen | Passed close date, champion change, budget |

## HubSpot

### Try it on your own deals (quickest)

1. In HubSpot, go to **Settings → Integrations → Private Apps** and create an app. Give it the scopes listed in `app/hubspot_app.py`.
2. In `.env`, set `CRM_PROVIDER=hubspot` and `HUBSPOT_ACCESS_TOKEN=<the app's token>`.
3. Run the server and call `POST /api/deals/<HubSpot deal ID>/analyze`.

### The public app and deal card

Requires Node.js and the HubSpot CLI (`npm install -g @hubspot/cli`).

1. Deploy the API to a public HTTPS URL (see Deploy below).
2. Replace `https://api.dealpulse.example` with that URL in three files: `hubspot/src/app/app-hsmeta.json`, `hubspot/src/app/cards/DealPulseCard.jsx` and `.env`.
3. Upload the project:
   ```bash
   cd hubspot
   hs account auth
   hs project install-deps
   hs project upload
   ```
4. Copy the app's client ID and secret from HubSpot into `.env` (`HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET`).
5. Install it in a test account by visiting `https://<your API>/hubspot/install`.
6. Open any deal. The DealPulse card is in the right sidebar.

On install, DealPulse:
- creates two deal properties, "Decision maker (DealPulse)" and "Pain points (DealPulse)"
- records the account and the installing user

To list installs as CSV, run `python -m app.installs`.

## Deploy

Any host that runs Python works (Render, Railway, Fly.io).

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Environment:** the variables from `.env.example`.
- **Storage:** installs are saved in SQLite at `DATABASE_PATH`. Point it at a persistent disk, or installs are lost on every redeploy.

## Tests

```bash
pytest                          # fast offline tests: the AI and HubSpot are replaced with fakes
RUN_EVALS=1 pytest -m eval -v   # runs the real AI on the sample deals (uses API credits)
```

On Windows PowerShell: `$env:RUN_EVALS=1; pytest -m eval -v`

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | |
| `ANTHROPIC_EFFORT` | `medium` | `low` is faster and cheaper; `high` is more thorough |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated origins allowed to call the API from another site |
| `CRM_PROVIDER` | `sample` | `hubspot` points `/api/deals` at one HubSpot account |
| `HUBSPOT_ACCESS_TOKEN` | — | Private app token, used when `CRM_PROVIDER=hubspot` |
| `HUBSPOT_CLIENT_ID` / `HUBSPOT_CLIENT_SECRET` | — | From the public app's Auth tab |
| `HUBSPOT_REDIRECT_URI` | — | `https://<your API>/hubspot/oauth/callback` |
| `PUBLIC_BASE_URL` | — | The API's public URL. Needed behind a proxy to verify HubSpot signatures |
| `DATABASE_PATH` | `dealpulse.db` | Where installs are stored |

## Roadmap

- [x] Analysis API, sample CRM, offline tests, AI evals
- [x] Demo page
- [x] HubSpot connector: OAuth, read deal activities, write approved fields
- [x] HubSpot deal card (code written; needs uploading with the HubSpot CLI)
- [ ] Run the AI evals with a real API key and tune the prompt
- [ ] Deploy
- [ ] Pipedrive connector
