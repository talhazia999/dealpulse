"""HubSpot connector and app tests. HubSpot is replaced with an in-memory fake, so these run offline."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.crm import hubspot
from app.crm.hubspot import HubSpotCRM
from app.hubspot_app import get_install_store, signature_is_valid
from app.installs import InstallStore
from app.main import app, get_analyzer
from app.schemas import FieldUpdate
from tests.test_api import FakeAnalyzer

SECRET = "test-client-secret"


class FakeHubSpot:
    """Answers the HubSpot API calls the connector makes, and records writes."""

    def __init__(self):
        self.patched: dict = {}
        self.created_properties: list[str] = []
        self.deal_props = {
            "dealname": "Acme — Annual", "amount": "25000", "deal_currency_code": "USD",
            "dealstage": "proposal", "pipeline": "default", "closedate": "2026-11-15T00:00:00Z",
            "createdate": "2026-09-01T10:00:00Z", "hubspot_owner_id": "77", "hs_next_step": "",
            "dealpulse_decision_maker": None, "dealpulse_pain_points": None,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        is_json = request.headers.get("Content-Type", "").startswith("application/json")
        body = json.loads(request.content) if is_json else {}

        if path == "/crm/v3/objects/deals/404":
            return httpx.Response(404, json={"message": "not found"})
        if path == "/crm/v3/objects/deals/1" and method == "GET":
            return httpx.Response(200, json={
                "id": "1",
                "properties": {**self.deal_props, **self.patched},
                "propertiesWithHistory": {"dealstage": [
                    {"value": "proposal", "timestamp": "2026-10-01T09:00:00Z"},
                    {"value": "qualified", "timestamp": "2026-09-01T10:00:00Z"},
                ]},
                "associations": {
                    "contacts": {"results": [{"id": "c1"}, {"id": "c2"}]},
                    "companies": {"results": [{"id": "co1"}]},
                    "emails": {"results": [{"id": "e1"}, {"id": "e2"}]},
                    "notes": {"results": [{"id": "n1"}]},
                    "calls": {"results": [{"id": "k1"}]},
                },
            })
        if path == "/crm/v3/objects/deals/1" and method == "PATCH":
            self.patched.update(body["properties"])
            return httpx.Response(200, json={"id": "1"})
        if path == "/crm/v3/pipelines/deals":
            return httpx.Response(200, json={"results": [{"id": "default", "stages": [
                {"id": "qualified", "label": "Qualified"}, {"id": "proposal", "label": "Proposal sent"}]}]})
        if path == "/crm/v3/owners/77":
            return httpx.Response(200, json={"id": "77", "firstName": "Sam", "lastName": "Rivera"})
        if path == "/crm/v3/objects/companies/batch/read":
            return httpx.Response(200, json={"results": [{"id": "co1", "properties": {"name": "Acme Corp"}}]})
        if path == "/crm/v3/objects/contacts/batch/read":
            return httpx.Response(200, json={"results": [
                {"id": "c1", "properties": {"firstname": "Priya", "lastname": "Nair", "jobtitle": "Ops",
                                            "email": "priya@acme.example", "hs_buying_role": "CHAMPION"}},
                {"id": "c2", "properties": {"firstname": "Ana", "lastname": "Diaz", "jobtitle": "CFO",
                                            "email": "ana@acme.example", "hs_buying_role": "BUDGET_HOLDER;DECISION_MAKER"}},
            ]})
        if path == "/crm/v3/objects/emails/batch/read":
            return httpx.Response(200, json={"results": [
                {"id": "e1", "properties": {"hs_timestamp": "2026-10-02T12:00:00Z", "hs_email_direction": "EMAIL",
                                            "hs_email_subject": "Proposal", "hs_email_text": "Here is the proposal."}},
                {"id": "e2", "properties": {"hs_timestamp": "2026-10-05T12:00:00Z", "hs_email_direction": "INCOMING_EMAIL",
                                            "hs_email_subject": "Re: Proposal", "hs_email_text": None,
                                            "hs_email_html": "<p>Looks good &amp; thanks.</p>"}},
            ]})
        if path == "/crm/v3/objects/notes/batch/read":
            return httpx.Response(200, json={"results": [
                {"id": "n1", "properties": {"hs_timestamp": "2026-10-03T08:00:00Z", "hs_note_body": "<div>Competitor in play</div>"}}]})
        if path == "/crm/v3/objects/calls/batch/read":
            return httpx.Response(200, json={"results": [
                {"id": "k1", "properties": {"hs_timestamp": "2026-09-20T15:00:00Z", "hs_call_title": "Discovery",
                                            "hs_call_body": "Pain: manual reporting", "hs_call_duration": "1800000"}}]})
        if path.startswith("/crm/v4/associations/") and path.endswith("/contacts/batch/read"):
            return httpx.Response(200, json={"results": [
                {"from": {"id": i["id"]}, "to": [{"toObjectId": "c1"}, {"toObjectId": "not-on-deal"}]}
                for i in body["inputs"]]})
        if path == "/crm/v3/properties/deals" and method == "POST":
            self.created_properties.append(body["name"])
            return httpx.Response(201, json=body)
        if path == "/crm/v3/objects/deals/search":
            return httpx.Response(200, json={"results": [{"id": "1", "properties": self.deal_props}]})
        if path == "/oauth/2026-03/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["client_secret"] == SECRET
            return httpx.Response(200, json={"access_token": "at-new", "refresh_token": "rt-1", "expires_in": 1800, "hub_id": 555})
        if path == "/oauth/2026-03/token/introspect":
            return httpx.Response(200, json={"active": True, "hub_id": 555, "hub_domain": "acme.hubspot.com", "user": "owner@acme.example"})
        if request.headers.get("Authorization") == "Bearer revoked":
            return httpx.Response(401, json={})
        return httpx.Response(500, json={"unexpected": path})


@pytest.fixture
def fake():
    fake = FakeHubSpot()
    hubspot._http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    yield fake
    hubspot._http = None


def test_reads_a_deal_with_its_activity(fake):
    deal = asyncio.run(HubSpotCRM("token").get_deal("1"))

    assert (deal.company, deal.amount, deal.stage, deal.owner_name) == ("Acme Corp", 25000, "Proposal sent", "Sam Rivera")
    assert deal.stage_entered_date == date(2026, 10, 1)
    assert deal.close_date == date(2026, 11, 15)
    assert [c.is_decision_maker for c in deal.contacts] == [False, True]
    assert [a.id for a in deal.activities] == ["e2", "n1", "e1", "k1"]  # newest first

    by_id = {a.id: a for a in deal.activities}
    assert by_id["e2"].direction == "inbound" and by_id["e2"].body == "Looks good & thanks."
    assert by_id["e1"].direction == "outbound"
    assert by_id["n1"].body == "Competitor in play"
    assert by_id["k1"].duration_minutes == 30
    assert by_id["e1"].contact_ids == ["c1"]  # contacts not on the deal are dropped


def test_missing_deal_raises_not_found(fake):
    from app.crm.base import DealNotFound

    with pytest.raises(DealNotFound):
        asyncio.run(HubSpotCRM("token").get_deal("404"))


def test_writes_approved_fields_to_hubspot_properties(fake):
    updates = [FieldUpdate(field="next_step", value="Call Ana", reason="r"),
               FieldUpdate(field="decision_maker", value="Ana Diaz (CFO)", reason="r")]
    deal = asyncio.run(HubSpotCRM("token").update_deal_fields("1", updates))
    assert fake.patched == {"hs_next_step": "Call Ana", "dealpulse_decision_maker": "Ana Diaz (CFO)"}
    assert deal.next_step == "Call Ana" and deal.decision_maker == "Ana Diaz (CFO)"


def test_signature_check():
    url, body, ts = "https://api.example.com/hubspot/card/analyze?portalId=555", b'{"dealId":"1"}', "1760000000000"
    sig = _sign("POST", url, body, ts)
    ok = dict(method="POST", url=url, body=body, timestamp=ts, signature=sig, secret=SECRET, now_ms=int(ts) + 1000)

    assert signature_is_valid(**ok)
    assert not signature_is_valid(**{**ok, "body": b'{"dealId":"2"}'})
    assert not signature_is_valid(**{**ok, "secret": "wrong"})
    assert not signature_is_valid(**{**ok, "now_ms": int(ts) + 6 * 60 * 1000})  # older than 5 minutes
    assert signature_is_valid(**{**ok, "url": url.replace("?", "%3F")})  # encoded characters are decoded first


# -- The HubSpot app: install and card endpoints ------------------------------


@pytest.fixture
def hub_client(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HUBSPOT_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "HUBSPOT_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(config, "HUBSPOT_REDIRECT_URI", "https://api.example.com/hubspot/oauth/callback")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://api.example.com")
    store = InstallStore(str(tmp_path / "test.db"))
    app.dependency_overrides[get_install_store] = lambda: store
    app.dependency_overrides[get_analyzer] = lambda: FakeAnalyzer()
    yield TestClient(app, base_url="https://testserver"), store
    app.dependency_overrides.clear()


def test_install_saves_the_account_and_creates_properties(hub_client, fake):
    client, store = hub_client
    start = client.get("/hubspot/install", follow_redirects=False)
    assert start.headers["location"].startswith("https://app.hubspot.com/oauth/authorize?")
    state = client.cookies.get("dp_oauth_state")

    done = client.get(f"/hubspot/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert done.headers["location"] == "/installed.html"
    install = store.get(555)
    assert (install.access_token, install.hub_domain, install.user_email) == ("at-new", "acme.hubspot.com", "owner@acme.example")
    assert fake.created_properties == ["dealpulse_decision_maker", "dealpulse_pain_points"]


def test_install_rejects_a_mismatched_state(hub_client):
    client, _ = hub_client
    client.get("/hubspot/install", follow_redirects=False)
    assert client.get("/hubspot/oauth/callback?code=abc&state=forged", follow_redirects=False).status_code == 400


def test_card_analyze_requires_a_valid_signature(hub_client):
    client, store = hub_client
    store.save(555, access_token="token", refresh_token="rt", expires_in=1800)
    body = json.dumps({"dealId": "1"}).encode()

    assert _card_post(client, "/hubspot/card/analyze", body, signature="bad").status_code == 401
    response = _card_post(client, "/hubspot/card/analyze", body)
    assert response.status_code == 200
    card = response.json()
    assert card["analysis"]["deal_id"] == "1"
    assert card["evidence_labels"]["e1"] == "Email · Oct 2 · Proposal"
    assert card["evidence_labels"]["deal_fields"] == "Deal fields"


def test_card_refreshes_an_expired_token(hub_client):
    client, store = hub_client
    store.save(555, access_token="old", refresh_token="rt-old", expires_in=-10)
    response = _card_post(client, "/hubspot/card/apply-updates",
                          json.dumps({"dealId": "1", "updates": [{"field": "pain_points", "value": "Manual reporting", "reason": "call"}]}).encode())
    assert response.status_code == 200
    assert store.get(555).access_token == "at-new"


def test_card_for_an_account_that_never_installed(hub_client):
    client, _ = hub_client
    response = _card_post(client, "/hubspot/card/analyze", b'{"dealId":"1"}')
    assert response.status_code == 401


def _sign(method: str, url: str, body: bytes, ts: str) -> str:
    message = method.encode() + url.encode() + body + ts.encode()
    return base64.b64encode(hmac.new(SECRET.encode(), message, hashlib.sha256).digest()).decode()


def _card_post(client: TestClient, path: str, body: bytes, signature: str | None = None):
    query = "?portalId=555&userId=1&userEmail=rep%40acme.example&appId=9"
    ts = str(int(time.time() * 1000))
    # HubSpot signs the decoded URL; %40 is one of the characters it decodes.
    signed_url = f"https://api.example.com{path}{query}".replace("%40", "@")
    headers = {
        "Content-Type": "application/json",
        "X-HubSpot-Request-Timestamp": ts,
        "X-HubSpot-Signature-v3": signature or _sign("POST", signed_url, body, ts),
    }
    return client.post(path + query, content=body, headers=headers)
