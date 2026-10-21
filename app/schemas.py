"""Data models shared by the API, the CRM connectors and the AI analyzer."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# CRM input: a deal and everything attached to it
# ---------------------------------------------------------------------------

ActivityType = Literal["email", "call", "note", "meeting"]
Direction = Literal["inbound", "outbound"]


class Contact(BaseModel):
    id: str
    name: str
    title: str | None = None
    email: str | None = None
    is_decision_maker: bool = False


class Activity(BaseModel):
    id: str
    type: ActivityType
    date: date
    direction: Direction | None = None  # emails only: inbound = from the prospect
    subject: str | None = None
    body: str
    contact_ids: list[str] = []
    duration_minutes: int | None = None  # calls and meetings


class Deal(BaseModel):
    id: str
    name: str
    company: str
    amount: float
    currency: str = "USD"
    stage: str
    stage_entered_date: date
    close_date: date | None = None  # many CRMs leave the expected close date empty
    owner_name: str
    next_step: str | None = None
    decision_maker: str | None = None
    pain_points: str | None = None
    contacts: list[Contact] = []
    activities: list[Activity] = []
    # The date the analysis treats as "today". Sample deals pin it so demos stay reproducible.
    as_of: date | None = None


class DealSummary(BaseModel):
    id: str
    name: str
    company: str
    amount: float
    currency: str
    stage: str


# ---------------------------------------------------------------------------
# Signals computed in code before the AI call, so dates and counts are exact
# ---------------------------------------------------------------------------


class DealSignals(BaseModel):
    as_of: date
    days_in_stage: int
    days_to_close: int | None  # negative when the close date has passed; None when no close date is set
    days_since_last_activity: int | None
    days_since_last_inbound: int | None
    unanswered_outbound_days: int | None  # days since our latest email went unanswered
    has_decision_maker_contact: bool
    activity_counts: dict[str, int]


# ---------------------------------------------------------------------------
# AI output: the structured analysis Claude returns
# ---------------------------------------------------------------------------

Severity = Literal["high", "medium", "low"]
RiskCategory = Literal[
    "no_response",
    "decision_maker_missing",
    "stalled_stage",
    "close_date",
    "competitor",
    "champion_change",
    "budget",
    "other",
]
UpdatableField = Literal["next_step", "decision_maker", "pain_points"]


class Risk(BaseModel):
    severity: Severity
    category: RiskCategory
    title: str = Field(description="One short sentence a sales rep can scan.")
    evidence: list[str] = Field(
        description='IDs of the activities that support this risk, or "deal_fields" for deal properties.'
    )


class FollowUpEmail(BaseModel):
    to_contact_id: str
    subject: str
    body: str


class FieldUpdate(BaseModel):
    field: UpdatableField
    value: str
    reason: str = Field(description="Why the deal history supports this value.")


class AnalysisResult(BaseModel):
    summary: str
    risks: list[Risk]
    next_step: str
    follow_up_email: FollowUpEmail
    field_updates: list[FieldUpdate]


class DealAnalysis(BaseModel):
    deal_id: str
    model: str
    signals: DealSignals
    result: AnalysisResult


class ApplyUpdatesRequest(BaseModel):
    updates: list[FieldUpdate]
