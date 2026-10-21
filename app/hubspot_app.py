"""The HubSpot app: OAuth install flow, and the endpoints the DealPulse card on each deal record calls.

Card requests come through `hubspot.fetch`, which signs every request with the app's client secret.
We verify that signature before touching any data, then use the installing account's OAuth token."""

import base64
import hashlib
import hmac
import secrets
import time
from functools import lru_cache
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app import config
from app.analyzer import DEAL_FIELDS_EVIDENCE, DealAnalyzer
from app.crm.http import shared_http
from app.crm.hubspot import HubSpotCRM
from app.installs import InstallStore
from app.schemas import Contact, Deal, DealAnalysis, FieldUpdate
from app.service import get_analyzer, load_deal, run_analysis, save_updates

AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
# The dated OAuth endpoints replace /oauth/v1, which HubSpot retires in February 2027.
TOKEN_URL = "https://api.hubapi.com/oauth/2026-03/token"
INTROSPECT_URL = "https://api.hubapi.com/oauth/2026-03/token/introspect"
# Must match requiredScopes in hubspot/src/app/app-hsmeta.json.
SCOPES = [
    "crm.objects.deals.read",
    "crm.objects.deals.write",
    "crm.objects.contacts.read",
    "crm.objects.companies.read",
    "crm.objects.owners.read",
    "crm.schemas.deals.read",
    "crm.schemas.deals.write",
    "sales-email-read",
]
STATE_COOKIE = "dp_oauth_state"
SIGNATURE_MAX_AGE_MS = 5 * 60 * 1000
# hubspot.fetch percent-encodes these characters; the v3 signature is computed over the decoded URL.
SIGNATURE_DECODE = {
    "%3A": ":", "%2F": "/", "%3F": "?", "%40": "@", "%21": "!", "%24": "$", "%27": "'",
    "%28": "(", "%29": ")", "%2A": "*", "%2C": ",", "%3B": ";",
}

router = APIRouter(prefix="/hubspot", tags=["hubspot"])


@lru_cache
def get_install_store() -> InstallStore:
    return InstallStore()


def require_hubspot_config() -> None:
    if not (config.HUBSPOT_CLIENT_ID and config.HUBSPOT_CLIENT_SECRET and config.HUBSPOT_REDIRECT_URI):
        raise HTTPException(status_code=503, detail="HubSpot app is not configured. Set the HUBSPOT_* variables in .env.")


# ---------------------------------------------------------------------------
# OAuth install flow
# ---------------------------------------------------------------------------


@router.get("/install", dependencies=[Depends(require_hubspot_config)])
async def install() -> RedirectResponse:
    """Send the user to HubSpot to choose an account and approve DealPulse."""
    state = secrets.token_urlsafe(24)
    query = urlencode({
        "client_id": config.HUBSPOT_CLIENT_ID,
        "redirect_uri": config.HUBSPOT_REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
    })
    response = RedirectResponse(f"{AUTHORIZE_URL}?{query}")
    response.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, secure=True, samesite="lax")
    return response


@router.get("/oauth/callback", dependencies=[Depends(require_hubspot_config)])
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    store: InstallStore = Depends(get_install_store),
) -> RedirectResponse:
    if error or not code:
        raise HTTPException(status_code=400, detail=f"HubSpot install was not completed: {error or 'no code'}.")
    # Installs started from /hubspot/install carry a state cookie that must match. Installs started
    # from the HubSpot marketplace come straight to this callback, so there is no cookie to check.
    expected = request.cookies.get(STATE_COOKIE)
    if expected and not hmac.compare_digest(expected, state or ""):
        raise HTTPException(status_code=400, detail="Install link expired. Start the install again.")

    tokens = await _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.HUBSPOT_REDIRECT_URI,
    })
    info = await _introspect(tokens["access_token"])
    hub_id = int(tokens.get("hub_id") or info["hub_id"])
    store.save(
        hub_id,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_in=int(tokens.get("expires_in", 1800)),
        hub_domain=info.get("hub_domain"),
        user_email=info.get("user"),
    )
    await HubSpotCRM(tokens["access_token"]).ensure_properties()

    response = RedirectResponse("/installed.html")
    response.delete_cookie(STATE_COOKIE)
    return response


async def _token_request(form: dict) -> dict:
    body = {"client_id": config.HUBSPOT_CLIENT_ID, "client_secret": config.HUBSPOT_CLIENT_SECRET, **form}
    response = await shared_http().post(TOKEN_URL, data=body)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="HubSpot did not accept the authorization. Try installing again.")
    return response.json()


async def _introspect(access_token: str) -> dict:
    body = {
        "client_id": config.HUBSPOT_CLIENT_ID,
        "client_secret": config.HUBSPOT_CLIENT_SECRET,
        "token_type_hint": "access_token",
        "token": access_token,
    }
    response = await shared_http().post(INTROSPECT_URL, data=body)
    return response.json() if response.status_code < 400 else {}


async def access_token_for(hub_id: int, store: InstallStore) -> str:
    """The account's access token, refreshed first if it is about to expire (they last 30 minutes)."""
    install = store.get(hub_id)
    if install is None:
        raise HTTPException(status_code=401, detail="DealPulse is not installed in this HubSpot account.")
    if install.expires_at - time.time() > 60:
        return install.access_token
    tokens = await _token_request({"grant_type": "refresh_token", "refresh_token": install.refresh_token})
    store.save(
        hub_id,
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token", install.refresh_token),
        expires_in=int(tokens.get("expires_in", 1800)),
    )
    return tokens["access_token"]


# ---------------------------------------------------------------------------
# Request signature (v3)
# ---------------------------------------------------------------------------


def signature_is_valid(
    *, method: str, url: str, body: bytes, timestamp: str, signature: str, secret: str, now_ms: int | None = None
) -> bool:
    try:
        age = (now_ms if now_ms is not None else int(time.time() * 1000)) - int(timestamp)
    except ValueError:
        return False
    if abs(age) > SIGNATURE_MAX_AGE_MS:
        return False
    url = url.split("#", 1)[0]
    for encoded, char in SIGNATURE_DECODE.items():
        url = url.replace(encoded, char).replace(encoded.lower(), char)
    message = method.upper().encode() + url.encode() + body + timestamp.encode()
    expected = base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


async def verified_portal_id(request: Request) -> int:
    """Reject requests that were not signed by HubSpot for this app; return the HubSpot account ID."""
    require_hubspot_config()
    # Behind a proxy the app sees an internal URL, so rebuild the public one HubSpot signed.
    url = str(request.url)
    if config.PUBLIC_BASE_URL:
        url = config.PUBLIC_BASE_URL.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    valid = signature_is_valid(
        method=request.method,
        url=url,
        body=await request.body(),
        timestamp=request.headers.get("X-HubSpot-Request-Timestamp", ""),
        signature=request.headers.get("X-HubSpot-Signature-v3", ""),
        secret=config.HUBSPOT_CLIENT_SECRET,
    )
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid HubSpot signature.")
    try:
        return int(request.query_params["portalId"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Missing portalId.") from None


async def card_crm(
    portal_id: int = Depends(verified_portal_id), store: InstallStore = Depends(get_install_store)
) -> HubSpotCRM:
    return HubSpotCRM(await access_token_for(portal_id, store))


# ---------------------------------------------------------------------------
# Card endpoints
# ---------------------------------------------------------------------------


class CardAnalyzeRequest(BaseModel):
    dealId: str


class CardApplyRequest(BaseModel):
    dealId: str
    updates: list[FieldUpdate]


class CardAnalysis(BaseModel):
    analysis: DealAnalysis
    # What the card needs to show without a second request: who the email goes to,
    # and a readable label for each piece of evidence ("Email · Oct 3 · Proposal").
    recipient: Contact | None
    evidence_labels: dict[str, str]


@router.post("/card/analyze")
async def card_analyze(
    body: CardAnalyzeRequest, crm: HubSpotCRM = Depends(card_crm), analyzer: DealAnalyzer = Depends(get_analyzer)
) -> CardAnalysis:
    deal = await load_deal(crm, body.dealId)
    analysis = await run_analysis(analyzer, deal)
    labels = {
        a.id: " · ".join(filter(None, [a.type.capitalize(), f"{a.date:%b} {a.date.day}", a.subject]))
        for a in deal.activities
    }
    labels[DEAL_FIELDS_EVIDENCE] = "Deal fields"
    recipient = next((c for c in deal.contacts if c.id == analysis.result.follow_up_email.to_contact_id), None)
    return CardAnalysis(analysis=analysis, recipient=recipient, evidence_labels=labels)


@router.post("/card/apply-updates")
async def card_apply(body: CardApplyRequest, crm: HubSpotCRM = Depends(card_crm)) -> Deal:
    return await save_updates(crm, body.dealId, body.updates)
