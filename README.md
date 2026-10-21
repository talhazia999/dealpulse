# DealPulse

**An AI deal assistant for B2B CRMs.** Open any deal, click once, and DealPulse reads the deal's emails, notes and call logs, then returns:

- a short **summary** of where the deal stands
- **risk flags** (no reply, no decision-maker, stalled stage, passed close date, competitor, champion left, budget), each linked to the activity it came from
- the single best **next step**
- a ready-to-send **follow-up email**
- **CRM field updates** that are written back only after the rep approves them

This repository is the Python backend. A HubSpot app card and a public demo page call the same API.

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

## Architecture

```
HubSpot card / demo page
        │  HTTPS
        ▼
FastAPI  (app/main.py)
        │
        ├── CRM connector  (app/crm/)   get deal · write approved fields
        ├── Signals        (app/signals.py)   exact dates and counts
        └── Analyzer       (app/analyzer.py)  Claude, structured output → validated result
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

Interactive API docs: http://localhost:8010/docs

## API

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/deals` | List the deals available from the connected CRM |
| `GET` | `/api/deals/{id}` | Get a deal with its contacts and activities |
| `POST` | `/api/deals/{id}/analyze` | Analyze a deal from the CRM |
| `POST` | `/api/analyze` | Analyze a deal sent in the request body |
| `POST` | `/api/deals/{id}/apply-updates` | Write rep-approved field updates back to the CRM |

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

## Tests

```bash
pytest                          # fast offline tests, the AI is replaced with a fake
RUN_EVALS=1 pytest -m eval -v   # runs the real AI on the sample deals (uses API credits)
```

On Windows PowerShell: `$env:RUN_EVALS=1; pytest -m eval -v`

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | |
| `ANTHROPIC_EFFORT` | `medium` | `low` is faster and cheaper; `high` is more thorough |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated origins of the demo page |

## Roadmap

- [x] Analysis API, sample CRM, offline tests, AI evals
- [ ] Public demo page
- [ ] HubSpot connector: OAuth, read deal activities, write approved fields
- [ ] HubSpot app card
- [ ] Pipedrive connector
