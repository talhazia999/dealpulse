"""The steps every entry point shares (demo API and HubSpot card): load a deal, analyze it, save
approved updates. Each step turns CRM and AI errors into clear HTTP responses."""

from functools import lru_cache

import anthropic
from fastapi import HTTPException

from app import config
from app.analyzer import AnalysisError, DealAnalyzer
from app.crm.base import CRMAuthError, CRMConnector, DealNotFound
from app.schemas import Deal, DealAnalysis, FieldUpdate


@lru_cache
def get_analyzer() -> DealAnalyzer:
    return DealAnalyzer()


async def load_deal(crm: CRMConnector, deal_id: str) -> Deal:
    try:
        return await crm.get_deal(deal_id)
    except DealNotFound:
        raise HTTPException(status_code=404, detail="Deal not found") from None
    except CRMAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def save_updates(crm: CRMConnector, deal_id: str, updates: list[FieldUpdate]) -> Deal:
    try:
        return await crm.update_deal_fields(deal_id, updates)
    except DealNotFound:
        raise HTTPException(status_code=404, detail="Deal not found") from None
    except CRMAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


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
