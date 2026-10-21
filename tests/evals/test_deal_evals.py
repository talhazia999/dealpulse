"""Runs the real AI analysis on each sample deal and checks it catches what a good sales manager would.

These call the Claude API and cost money, so they only run when asked:
    RUN_EVALS=1 pytest -m eval -v
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.analyzer import DealAnalyzer
from app.crm.sample import SampleCRM

EXPECTATIONS = json.loads((Path(__file__).parent / "expectations.json").read_text(encoding="utf-8"))

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(os.getenv("RUN_EVALS") != "1", reason="set RUN_EVALS=1 to run the AI evals"),
]


@pytest.fixture(scope="module")
def analyses():
    async def run_all():
        crm, analyzer = SampleCRM(), DealAnalyzer()
        deals = [await crm.get_deal(deal_id) for deal_id in EXPECTATIONS]
        results = await asyncio.gather(*(analyzer.analyze(d) for d in deals))
        return {deal.id: (deal, result) for deal, result in zip(deals, results)}

    return asyncio.run(run_all())


@pytest.mark.parametrize("deal_id", list(EXPECTATIONS))
def test_flags_the_expected_risks(analyses, deal_id):
    expected = EXPECTATIONS[deal_id]
    _, analysis = analyses[deal_id]
    risks = analysis.result.risks
    categories = {r.category for r in risks}
    high = [r for r in risks if r.severity == "high"]

    missing = set(expected["must_flag"]) - categories
    assert not missing, f"missed risks {missing}; got {[(r.category, r.title) for r in risks]}"
    if expected["needs_high_risk"]:
        assert high, "expected at least one high-severity risk"
    if not expected["allow_high_risk"]:
        assert not high, f"healthy deal was over-flagged: {[r.title for r in high]}"


@pytest.mark.parametrize("deal_id", list(EXPECTATIONS))
def test_every_risk_cites_evidence(analyses, deal_id):
    _, analysis = analyses[deal_id]
    for risk in analysis.result.risks:
        assert risk.evidence, f"risk without evidence: {risk.title}"


@pytest.mark.parametrize("deal_id", list(EXPECTATIONS))
def test_follow_up_email_is_usable(analyses, deal_id):
    deal, analysis = analyses[deal_id]
    email = analysis.result.follow_up_email
    assert email.to_contact_id in {c.id for c in deal.contacts}
    assert len(email.body.split()) <= 150, "email is too long to send as-is"
    assert "[" not in email.body, "email contains a placeholder"


@pytest.mark.parametrize("deal_id", list(EXPECTATIONS))
def test_suggests_the_expected_field_updates(analyses, deal_id):
    _, analysis = analyses[deal_id]
    fields = {u.field for u in analysis.result.field_updates}
    missing = set(EXPECTATIONS[deal_id]["must_update"]) - fields
    assert not missing, f"expected updates to {missing}; got {fields}"
