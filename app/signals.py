"""Deterministic deal facts. Computed in code, not by the model, so the numbers are always right."""

from collections import Counter
from datetime import date

from app.schemas import Deal, DealSignals


def compute_signals(deal: Deal, today: date | None = None) -> DealSignals:
    as_of = today or deal.as_of or date.today()
    activities = [a for a in deal.activities if a.date <= as_of]

    last_activity = max((a.date for a in activities), default=None)
    emails = [a for a in activities if a.type == "email"]
    last_inbound = max((a.date for a in emails if a.direction == "inbound"), default=None)
    last_outbound = max((a.date for a in emails if a.direction == "outbound"), default=None)

    unanswered = None
    if last_outbound and (last_inbound is None or last_outbound > last_inbound):
        unanswered = (as_of - last_outbound).days

    return DealSignals(
        as_of=as_of,
        days_in_stage=(as_of - deal.stage_entered_date).days,
        days_to_close=(deal.close_date - as_of).days,
        days_since_last_activity=(as_of - last_activity).days if last_activity else None,
        days_since_last_inbound=(as_of - last_inbound).days if last_inbound else None,
        unanswered_outbound_days=unanswered,
        has_decision_maker_contact=any(c.is_decision_maker for c in deal.contacts),
        activity_counts=dict(Counter(a.type for a in activities)),
    )
