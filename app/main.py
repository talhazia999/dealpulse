import secrets
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import config
from app.analyzer import DealAnalyzer
from app.crm.base import CRMAuthError, CRMConnector
from app.crm.hubspot import HubSpotCRM
from app.crm.pipedrive import PipedriveCRM
from app.crm.sample import DemoSessions
from app.hubspot_app import router as hubspot_router
from app.schemas import ApplyUpdatesRequest, Deal, DealAnalysis, DealSignals, DealSummary
from app.service import get_analyzer, load_deal, run_analysis, save_updates
from app.signals import compute_signals

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEMO_COOKIE = "dp_demo"

app = FastAPI(
    title="DealPulse API",
    version="0.2.0",
    description="Reads a CRM deal's emails, notes and calls, and returns a summary, "
    "risk flags, the next step, a follow-up email and suggested field updates.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(hubspot_router)


@lru_cache
def demo_sessions() -> DemoSessions:
    return DemoSessions()


def get_crm(request: Request, response: Response) -> CRMConnector:
    """The CRM behind /api/deals. The public demo gives each visitor their own copy of the sample deals;
    CRM_PROVIDER=hubspot or pipedrive points it at one real account instead, for trying DealPulse on real deals."""
    if config.CRM_PROVIDER == "hubspot":
        return HubSpotCRM(config.HUBSPOT_ACCESS_TOKEN)
    if config.CRM_PROVIDER == "pipedrive":
        domain = config.PIPEDRIVE_COMPANY_DOMAIN
        if not domain.startswith("http"):
            domain = f"https://{domain}.pipedrive.com"
        return PipedriveCRM(config.PIPEDRIVE_API_TOKEN, domain)
    session_id = request.cookies.get(DEMO_COOKIE)
    if not session_id:
        session_id = secrets.token_urlsafe(16)
        response.set_cookie(DEMO_COOKIE, session_id, max_age=86400, httponly=True, samesite="lax")
    return demo_sessions().get(session_id)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/deals")
async def list_deals(crm: CRMConnector = Depends(get_crm)) -> list[DealSummary]:
    try:
        return await crm.list_deals()
    except CRMAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/api/deals/{deal_id}")
async def get_deal(deal_id: str, crm: CRMConnector = Depends(get_crm)) -> Deal:
    return await load_deal(crm, deal_id)


@app.get("/api/deals/{deal_id}/signals")
async def get_signals(deal_id: str, crm: CRMConnector = Depends(get_crm)) -> DealSignals:
    """The exact dates and counts, computed in code. Free and instant: no AI call."""
    return compute_signals(await load_deal(crm, deal_id))


@app.post("/api/deals/{deal_id}/analyze")
async def analyze_deal(
    deal_id: str,
    crm: CRMConnector = Depends(get_crm),
    analyzer: DealAnalyzer = Depends(get_analyzer),
) -> DealAnalysis:
    return await run_analysis(analyzer, await load_deal(crm, deal_id))


@app.post("/api/analyze")
async def analyze_payload(deal: Deal, analyzer: DealAnalyzer = Depends(get_analyzer)) -> DealAnalysis:
    """Analyze a deal sent in the request body, for CRMs that push data instead of being read."""
    return await run_analysis(analyzer, deal)


@app.post("/api/deals/{deal_id}/apply-updates")
async def apply_updates(
    deal_id: str, body: ApplyUpdatesRequest, crm: CRMConnector = Depends(get_crm)
) -> Deal:
    """Write the field updates the rep approved back to the CRM."""
    return await save_updates(crm, deal_id, body.updates)


# The demo page. Mounted last so the API routes above take precedence.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
