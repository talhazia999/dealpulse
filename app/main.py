from functools import lru_cache

import anthropic
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.analyzer import AnalysisError, DealAnalyzer
from app.crm.base import CRMConnector, DealNotFound
from app.crm.sample import SampleCRM
from app.schemas import ApplyUpdatesRequest, Deal, DealAnalysis, DealSummary

app = FastAPI(
    title="DealPulse API",
    version="0.1.0",
    description="Reads a CRM deal's emails, notes and calls, and returns a summary, "
    "risk flags, the next step, a follow-up email and suggested field updates.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@lru_cache
def get_crm() -> CRMConnector:
    return SampleCRM()


@lru_cache
def get_analyzer() -> DealAnalyzer:
    return DealAnalyzer()


async def run_analysis(analyzer: DealAnalyzer, deal: Deal) -> DealAnalysis:
    if isinstance(analyzer, DealAnalyzer) and not config.HAS_ANTHROPIC_CREDENTIALS:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not set. Add it to .env.")
    try:
        return await analyzer.analyze(deal)
    except AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except anthropic.RateLimitError as exc:
        raise HTTPException(status_code=429, detail="AI service is busy. Try again shortly.") from exc
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        raise HTTPException(status_code=503, detail="AI service is unavailable.") from exc


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/deals")
async def list_deals(crm: CRMConnector = Depends(get_crm)) -> list[DealSummary]:
    return await crm.list_deals()


@app.get("/api/deals/{deal_id}")
async def get_deal(deal_id: str, crm: CRMConnector = Depends(get_crm)) -> Deal:
    try:
        return await crm.get_deal(deal_id)
    except DealNotFound:
        raise HTTPException(status_code=404, detail="Deal not found") from None


@app.post("/api/deals/{deal_id}/analyze")
async def analyze_deal(
    deal_id: str,
    crm: CRMConnector = Depends(get_crm),
    analyzer: DealAnalyzer = Depends(get_analyzer),
) -> DealAnalysis:
    deal = await get_deal(deal_id, crm)
    return await run_analysis(analyzer, deal)


@app.post("/api/analyze")
async def analyze_payload(deal: Deal, analyzer: DealAnalyzer = Depends(get_analyzer)) -> DealAnalysis:
    """Analyze a deal sent in the request body, for CRMs that push data instead of being read."""
    return await run_analysis(analyzer, deal)


@app.post("/api/deals/{deal_id}/apply-updates")
async def apply_updates(
    deal_id: str, body: ApplyUpdatesRequest, crm: CRMConnector = Depends(get_crm)
) -> Deal:
    """Write the field updates the rep approved back to the CRM."""
    try:
        return await crm.update_deal_fields(deal_id, body.updates)
    except DealNotFound:
        raise HTTPException(status_code=404, detail="Deal not found") from None
