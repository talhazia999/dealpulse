import asyncio

from app.crm.sample import SampleCRM
from app.signals import compute_signals


def load(deal_id: str):
    return asyncio.run(SampleCRM().get_deal(deal_id))


def test_unanswered_proposal_is_counted():
    signals = compute_signals(load("northwind"))
    assert signals.unanswered_outbound_days == 16
    assert signals.days_in_stage == 21
    assert signals.days_to_close == 12
    assert signals.has_decision_maker_contact is False
    assert signals.activity_counts == {"email": 2, "call": 1, "note": 1}


def test_reply_after_outbound_clears_unanswered():
    signals = compute_signals(load("brightline"))
    assert signals.unanswered_outbound_days is None
    assert signals.days_since_last_inbound == 4


def test_passed_close_date_is_negative():
    signals = compute_signals(load("kestrel"))
    assert signals.days_to_close == -19
    assert signals.days_in_stage == 55
