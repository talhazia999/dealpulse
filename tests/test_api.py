"""API tests. The AI call is replaced with a fake, so these run offline and cost nothing."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.analyzer import DealAnalyzer, clean_result
from app.crm.sample import SampleCRM
from app.main import app, get_analyzer, get_crm
from app.schemas import AnalysisResult, Deal, DealAnalysis, FieldUpdate, FollowUpEmail, Risk
from app.signals import compute_signals

FAKE_RESULT = AnalysisResult(
    summary="Proposal sent, no reply yet.",
    risks=[Risk(severity="high", category="no_response", title="No reply in 16 days", evidence=["nw-a4"])],
    next_step="Follow up with Priya today.",
    follow_up_email=FollowUpEmail(to_contact_id="nw-priya", subject="Quick follow-up", body="Hi Priya, ..."),
    field_updates=[FieldUpdate(field="next_step", value="Get CFO on a call", reason="CFO approval needed")],
)


class FakeAnalyzer:
    async def analyze(self, deal: Deal) -> DealAnalysis:
        return DealAnalysis(deal_id=deal.id, model="fake", signals=compute_signals(deal), result=FAKE_RESULT)


@pytest.fixture
def client():
    crm = SampleCRM()
    app.dependency_overrides[get_crm] = lambda: crm
    app.dependency_overrides[get_analyzer] = lambda: FakeAnalyzer()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_lists_sample_deals(client):
    deals = client.get("/api/deals").json()
    assert [d["id"] for d in deals] == ["northwind", "brightline", "kestrel"]


def test_unknown_deal_returns_404(client):
    assert client.get("/api/deals/nope").status_code == 404
    assert client.post("/api/deals/nope/analyze").status_code == 404


def test_analyze_returns_signals_and_result(client):
    body = client.post("/api/deals/northwind/analyze").json()
    assert body["signals"]["unanswered_outbound_days"] == 16
    assert body["result"]["risks"][0]["category"] == "no_response"


def test_apply_updates_writes_back(client):
    payload = {"updates": [{"field": "decision_maker", "value": "CFO (name unknown)", "reason": "From call"}]}
    deal = client.post("/api/deals/northwind/apply-updates", json=payload).json()
    assert deal["decision_maker"] == "CFO (name unknown)"
    assert client.get("/api/deals/northwind").json()["decision_maker"] == "CFO (name unknown)"


def test_apply_updates_rejects_unknown_fields(client):
    payload = {"updates": [{"field": "amount", "value": "1", "reason": "x"}]}
    assert client.post("/api/deals/northwind/apply-updates", json=payload).status_code == 422


def test_demo_visitors_do_not_see_each_others_updates():
    alice, bob = TestClient(app), TestClient(app)
    alice.get("/api/deals")
    bob.get("/api/deals")
    payload = {"updates": [{"field": "next_step", "value": "Alice's step", "reason": "r"}]}
    alice.post("/api/deals/northwind/apply-updates", json=payload)

    assert alice.get("/api/deals/northwind").json()["next_step"] == "Alice's step"
    assert bob.get("/api/deals/northwind").json()["next_step"] is None


def test_signals_endpoint_needs_no_ai(client):
    signals = client.get("/api/deals/kestrel/signals").json()
    assert signals["days_to_close"] == -19
    assert client.get("/api/deals/nope/signals").status_code == 404


def test_demo_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "DealPulse" in response.text


def test_analyzer_caches_unchanged_deals():
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=FAKE_RESULT, model="test-model")

    fake_client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    analyzer = DealAnalyzer(client=fake_client)
    crm = SampleCRM()
    deal = asyncio.run(crm.get_deal("northwind"))

    asyncio.run(analyzer.analyze(deal))
    asyncio.run(analyzer.analyze(deal))
    assert len(calls) == 1

    changed = deal.model_copy(update={"next_step": "Call the CFO"})
    asyncio.run(analyzer.analyze(changed))
    assert len(calls) == 2


def test_clean_result_drops_made_up_evidence_and_contacts():
    deal = asyncio.run(SampleCRM().get_deal("northwind"))
    messy = FAKE_RESULT.model_copy(
        update={
            "risks": [Risk(severity="high", category="no_response", title="t", evidence=["nw-a4", "invented", "deal_fields"])],
            "follow_up_email": FollowUpEmail(to_contact_id="someone-else", subject="s", body="b"),
        }
    )
    cleaned = clean_result(messy, deal)
    assert cleaned.risks[0].evidence == ["nw-a4", "deal_fields"]
    assert cleaned.follow_up_email.to_contact_id == "nw-priya"
