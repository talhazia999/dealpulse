"""Pipedrive connector tests. Pipedrive is replaced with an in-memory fake, so these run offline."""

import asyncio
import json
from datetime import date

import httpx
import pytest

from app.crm.base import CRMAuthError, DealNotFound
from app.crm.pipedrive import PipedriveCRM
from app.schemas import FieldUpdate
from app.signals import compute_signals

DM_KEY = "a" * 40


class FakePipedrive:
    """Answers the Pipedrive API calls the connector makes, and records writes."""

    def __init__(self, mail_allowed: bool = True, fields_exist: bool = True):
        self.mail_allowed = mail_allowed
        self.fields = [{"field_name": "Title", "field_code": "title"}]
        if fields_exist:
            self.fields.append({"field_name": "Decision maker (DealPulse)", "field_code": DM_KEY})
        self.custom_fields = {DM_KEY: None}
        self.patches: list[dict] = []
        self.seen_headers: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen_headers.append(dict(request.headers))
        path, method, q = request.url.path, request.method, request.url.params
        body = json.loads(request.content) if request.content else {}

        if path == "/api/v2/deals/404":
            return httpx.Response(404, json={"success": False})
        if path == "/api/v2/deals/7" and method == "GET":
            return httpx.Response(200, json={"success": True, "data": {
                "id": 7, "title": "Globex — Pilot", "value": 12000, "currency": "EUR", "stage_id": 2,
                "owner_id": 11, "org_id": 5, "add_time": "2026-09-01T10:00:00Z",
                "stage_change_time": "2026-10-01T09:00:00Z", "expected_close_date": None,
                "custom_fields": dict(self.custom_fields),
            }})
        if path == "/api/v2/deals/7" and method == "PATCH":
            self.patches.append(body)
            self.custom_fields.update(body["custom_fields"])
            return httpx.Response(200, json={"success": True, "data": {"id": 7}})
        if path == "/api/v2/stages":
            return httpx.Response(200, json={"success": True, "data": [
                {"id": 1, "name": "Qualified"}, {"id": 2, "name": "Proposal made"}],
                "additional_data": {"next_cursor": None}})
        if path == "/api/v1/users/11":
            return httpx.Response(200, json={"success": True, "data": {"id": 11, "name": "Jordan Lee"}})
        if path == "/api/v2/organizations/5":
            return httpx.Response(200, json={"success": True, "data": {"id": 5, "name": "Globex Corp"}})
        if path == "/api/v2/dealFields" and method == "GET":
            return httpx.Response(200, json={"success": True, "data": self.fields, "additional_data": {"next_cursor": None}})
        if path == "/api/v2/dealFields" and method == "POST":
            code = body["field_name"][:2].lower() * 20
            self.fields.append({"field_name": body["field_name"], "field_code": code})
            return httpx.Response(200, json={"success": True, "data": {"field_code": code, "field_name": body["field_name"]}})
        if path == "/api/v2/persons":
            assert q["deal_id"] == "7"
            if q.get("cursor") is None:  # two pages, to exercise the cursor
                return httpx.Response(200, json={"success": True, "data": [
                    {"id": 21, "name": "Hank Scorpio", "job_title": "CEO",
                     "emails": [{"value": "old@globex.example", "primary": False}, {"value": "hank@globex.example", "primary": True}]}],
                    "additional_data": {"next_cursor": "page2"}})
            return httpx.Response(200, json={"success": True, "data": [
                {"id": 22, "name": "Mindy Lee", "job_title": None, "emails": []}],
                "additional_data": {"next_cursor": None}})
        if path == "/api/v2/activities":
            return httpx.Response(200, json={"success": True, "data": [
                {"id": 31, "type": "call", "subject": "Discovery", "note": "<p>Needs SSO</p>", "due_date": "2026-09-20",
                 "duration": "00:45", "done": True, "person_id": 21, "participants": [{"person_id": 22}, {"person_id": 99}]},
                {"id": 32, "type": "meeting", "subject": "Security review", "note": None, "due_date": "2026-10-25",
                 "duration": "01:00", "done": False, "person_id": 22, "participants": []},
                {"id": 33, "type": "task", "subject": "Send case study", "note": "", "due_date": "2026-10-02",
                 "done": True, "person_id": None},
            ], "additional_data": {"next_cursor": None}})
        if path == "/api/v1/notes":
            return httpx.Response(200, json={"success": True, "data": [
                {"id": 41, "content": "Competitor <b>Initech</b> is cheaper", "add_time": "2026-09-21 08:30:00", "person_id": None}]})
        if path == "/api/v1/deals/7/mailMessages":
            if not self.mail_allowed:
                return httpx.Response(403, json={"success": False})
            return httpx.Response(200, json={"success": True, "data": [
                {"object": "mailMessage", "data": {"id": 51, "subject": "Proposal", "body": "<p>Attached.</p>", "sent_flag": True,
                 "message_time": "2026-10-03T10:00:00Z", "to": [{"linked_person_id": 21}], "from": []}},
                {"object": "mailMessage", "data": {"id": 52, "subject": "Draft", "body": "x", "sent_flag": True, "draft_flag": True,
                 "message_time": "2026-10-04T10:00:00Z"}},
                {"object": "mailMessage", "data": {"id": 53, "subject": "Re: Proposal", "body": None, "snippet": "Looks good",
                 "sent_flag": False, "message_time": "2026-10-05T10:00:00Z", "from": [{"linked_person_id": 21}]}},
            ]})
        if path == "/api/v2/deals":
            return httpx.Response(200, json={"success": True, "data": [
                {"id": 7, "title": "Globex — Pilot", "value": 12000, "currency": "EUR", "stage_id": 2}]})
        return httpx.Response(500, json={"unexpected": path})


def crm_for(fake: FakePipedrive, auth: str = "api_token") -> PipedriveCRM:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return PipedriveCRM("secret-token", "https://globex.pipedrive.com", auth=auth, http=http)


def test_reads_a_deal_with_its_history():
    deal = asyncio.run(crm_for(FakePipedrive()).get_deal("7"))

    assert (deal.company, deal.amount, deal.currency, deal.stage, deal.owner_name) == (
        "Globex Corp", 12000, "EUR", "Proposal made", "Jordan Lee")
    assert deal.stage_entered_date == date(2026, 10, 1)
    assert deal.close_date is None
    assert [(c.name, c.email) for c in deal.contacts] == [("Hank Scorpio", "hank@globex.example"), ("Mindy Lee", None)]
    assert [a.id for a in deal.activities] == [
        "activity-32", "email-53", "email-51", "activity-33", "note-41", "activity-31"]  # newest first, draft skipped

    by_id = {a.id: a for a in deal.activities}
    assert by_id["activity-31"].type == "call" and by_id["activity-31"].duration_minutes == 45
    assert by_id["activity-31"].body == "Needs SSO"
    assert by_id["activity-31"].contact_ids == ["21", "22"]  # people not on the deal are dropped
    assert by_id["activity-32"].body.startswith("(Scheduled, not done yet)")
    assert by_id["activity-33"].type == "note" and by_id["activity-33"].subject == "Task: Send case study"
    assert by_id["note-41"].body == "Competitor Initech is cheaper"
    assert by_id["email-51"].direction == "outbound" and by_id["email-51"].body == "Attached."
    assert by_id["email-53"].direction == "inbound" and by_id["email-53"].body == "Looks good"


def test_signals_work_without_a_close_date():
    deal = asyncio.run(crm_for(FakePipedrive()).get_deal("7"))
    signals = compute_signals(deal, today=date(2026, 10, 19))
    assert signals.days_to_close is None
    assert signals.unanswered_outbound_days is None  # the prospect replied after our proposal


def test_missing_mail_permission_still_analyzes_the_deal():
    deal = asyncio.run(crm_for(FakePipedrive(mail_allowed=False)).get_deal("7"))
    assert not any(a.type == "email" for a in deal.activities)
    assert deal.activities  # notes, calls and meetings are still there


def test_missing_deal_raises_not_found():
    with pytest.raises(DealNotFound):
        asyncio.run(crm_for(FakePipedrive()).get_deal("404"))
    with pytest.raises(DealNotFound):
        asyncio.run(crm_for(FakePipedrive()).get_deal("northwind"))


def test_writes_approved_fields_and_creates_missing_custom_fields():
    fake = FakePipedrive()
    updates = [FieldUpdate(field="decision_maker", value="Hank Scorpio (CEO)", reason="r"),
               FieldUpdate(field="next_step", value="Book security review", reason="r")]
    deal = asyncio.run(crm_for(fake).update_deal_fields("7", updates))

    created = [f["field_name"] for f in fake.fields[2:]]
    assert created == ["Next step (DealPulse)", "Pain points (DealPulse)"]
    assert fake.patches[0]["custom_fields"][DM_KEY] == "Hank Scorpio (CEO)"
    assert deal.decision_maker == "Hank Scorpio (CEO)" and deal.next_step == "Book security review"
    assert [c.is_decision_maker for c in deal.contacts] == [True, False]


def test_auth_header_for_api_token_and_oauth():
    token_fake, oauth_fake = FakePipedrive(), FakePipedrive()
    asyncio.run(crm_for(token_fake).list_deals())
    asyncio.run(crm_for(oauth_fake, auth="oauth").list_deals())
    assert token_fake.seen_headers[0]["x-api-token"] == "secret-token"
    assert oauth_fake.seen_headers[0]["authorization"] == "Bearer secret-token"


def test_rejected_token_raises_auth_error():
    def deny(request):
        return httpx.Response(401, json={"success": False})

    crm = PipedriveCRM("bad", "https://globex.pipedrive.com", http=httpx.AsyncClient(transport=httpx.MockTransport(deny)))
    with pytest.raises(CRMAuthError):
        asyncio.run(crm.get_deal("7"))
