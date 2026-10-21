"""Pipedrive connector: reads a deal with its people, emails, notes, calls and meetings,
and writes rep-approved fields back.

Uses API v2 wherever it exists (deals, persons, organizations, stages, activities, deal fields).
Notes, emails and users have no v2 endpoint yet, so those calls use v1."""

import asyncio
import re
from typing import Literal

import httpx

from app.crm.base import CRMAuthError, DealNotFound
from app.crm.http import shared_http
from app.crm.text import MAX_ACTIVITIES, clip, strip_html, to_date, to_number
from app.schemas import Activity, Contact, Deal, DealSummary, FieldUpdate

# Pipedrive has no built-in "next step", "decision maker" or "pain points" field on deals, so DealPulse
# creates three long-text custom fields. Custom fields are addressed by a generated key, looked up by name.
FIELD_NAMES = {
    "next_step": "Next step (DealPulse)",
    "decision_maker": "Decision maker (DealPulse)",
    "pain_points": "Pain points (DealPulse)",
}
ACTIVITY_TYPES = {"call": "call", "meeting": "meeting"}
MAX_PAGES = 5


class PipedriveCRM:
    name = "pipedrive"

    def __init__(
        self,
        access_token: str,
        api_domain: str,
        auth: Literal["api_token", "oauth"] = "api_token",
        http: httpx.AsyncClient | None = None,
    ):
        """api_domain is the company's Pipedrive URL, e.g. https://acme.pipedrive.com.
        auth="api_token" for a personal API token; "oauth" for a Marketplace app access token."""
        self._http = http or shared_http()
        self._base = api_domain.rstrip("/")
        self._headers = {"x-api-token": access_token} if auth == "api_token" else {"Authorization": f"Bearer {access_token}"}
        self._stage_names: dict[int, str] | None = None
        self._field_keys: dict[str, str] | None = None

    # -- CRMConnector ------------------------------------------------------

    async def list_deals(self) -> list[DealSummary]:
        data = await self._request("GET", "/api/v2/deals", params={
            "status": "open", "sort_by": "update_time", "sort_direction": "desc", "limit": 20,
        })
        stages = await self._stage_names_by_id()
        return [
            DealSummary(
                id=str(d["id"]), name=d.get("title") or "Untitled deal", company=d.get("title") or "",
                amount=to_number(d.get("value")), currency=d.get("currency") or "USD",
                stage=stages.get(d.get("stage_id"), "Unknown stage"),
            )
            for d in data.get("data") or []
        ]

    async def get_deal(self, deal_id: str) -> Deal:
        if not deal_id.isdigit():
            raise DealNotFound(deal_id)
        deal = (await self._request("GET", f"/api/v2/deals/{deal_id}", not_found=deal_id))["data"]

        stages, owner, company, field_keys, people, activities, notes, emails = await asyncio.gather(
            self._stage_names_by_id(),
            self._owner_name(deal.get("owner_id")),
            self._org_name(deal.get("org_id")),
            self._fields(),
            self._paged("/api/v2/persons", {"deal_id": deal_id}),
            self._paged("/api/v2/activities", {"deal_id": deal_id}),
            self._notes(deal_id),
            self._emails(deal_id),
        )

        custom = deal.get("custom_fields") or {}
        values = {field: _text(custom.get(key)) for field, key in field_keys.items()}
        contacts = [_to_contact(p, values.get("decision_maker")) for p in people]
        person_ids = {c.id for c in contacts}

        history = [a for a in (_to_activity(x) for x in activities) if a] + notes + emails
        history.sort(key=lambda a: a.date, reverse=True)
        history = [
            a.model_copy(update={"contact_ids": [c for c in dict.fromkeys(a.contact_ids) if c in person_ids]})
            for a in history[:MAX_ACTIVITIES]
        ]

        added = to_date(deal.get("add_time"))
        return Deal(
            id=str(deal["id"]),
            name=deal.get("title") or "Untitled deal",
            company=company or deal.get("title") or "Unknown company",
            amount=to_number(deal.get("value")),
            currency=deal.get("currency") or "USD",
            stage=stages.get(deal.get("stage_id"), "Unknown stage"),
            stage_entered_date=to_date(deal.get("stage_change_time")) or added,
            close_date=to_date(deal.get("expected_close_date")),
            owner_name=owner,
            contacts=contacts,
            activities=history,
            **values,
        )

    async def update_deal_fields(self, deal_id: str, updates: list[FieldUpdate]) -> Deal:
        keys = await self.ensure_fields()
        body = {"custom_fields": {keys[u.field]: u.value for u in updates}}
        await self._request("PATCH", f"/api/v2/deals/{deal_id}", json=body, not_found=deal_id)
        return await self.get_deal(deal_id)

    # -- Setup ---------------------------------------------------------------

    async def ensure_fields(self) -> dict[str, str]:
        """Create any of DealPulse's custom deal fields that don't exist yet; return field → key."""
        keys = await self._fields()
        for field, name in FIELD_NAMES.items():
            if field not in keys:
                body = {"field_name": name, "field_type": "text",
                        "ui_visibility": {"add_visible_flag": False, "details_visible_flag": True}}
                created = (await self._request("POST", "/api/v2/dealFields", json=body))["data"]
                keys[field] = created["field_code"]
        return keys

    # -- Helpers -------------------------------------------------------------

    async def _request(self, method: str, path: str, *, not_found: str | None = None, **kwargs) -> dict:
        for attempt in range(2):
            response = await self._http.request(method, self._base + path, headers=self._headers, **kwargs)
            if response.status_code == 429 and attempt == 0:
                await asyncio.sleep(min(float(response.headers.get("x-ratelimit-reset", 2)), 10))
                continue
            break
        if response.status_code in (404, 410) and not_found is not None:
            raise DealNotFound(not_found)
        if response.status_code in (401, 403):
            raise CRMAuthError(f"Pipedrive rejected the request ({response.status_code}).")
        response.raise_for_status()
        return response.json() if response.content else {}

    async def _paged(self, path: str, params: dict) -> list[dict]:
        """Read a v2 list endpoint, following its cursor."""
        items, cursor = [], None
        for _ in range(MAX_PAGES):
            data = await self._request("GET", path, params={**params, "limit": 100, **({"cursor": cursor} if cursor else {})})
            items.extend(data.get("data") or [])
            cursor = (data.get("additional_data") or {}).get("next_cursor")
            if not cursor:
                break
        return items

    async def _fields(self) -> dict[str, str]:
        if self._field_keys is None:
            fields = await self._paged("/api/v2/dealFields", {})
            by_name = {f.get("field_name"): f.get("field_code") for f in fields}
            self._field_keys = {field: by_name[name] for field, name in FIELD_NAMES.items() if by_name.get(name)}
        return self._field_keys

    async def _stage_names_by_id(self) -> dict[int, str]:
        if self._stage_names is None:
            self._stage_names = {s["id"]: s["name"] for s in await self._paged("/api/v2/stages", {})}
        return self._stage_names

    async def _owner_name(self, owner_id: int | None) -> str:
        if not owner_id:
            return "Unassigned"
        try:
            user = (await self._request("GET", f"/api/v1/users/{owner_id}"))["data"]
        except (httpx.HTTPStatusError, CRMAuthError):
            return "Unknown owner"
        return user.get("name") or user.get("email") or "Unknown owner"

    async def _org_name(self, org_id: int | None) -> str | None:
        if not org_id:
            return None
        try:
            return (await self._request("GET", f"/api/v2/organizations/{org_id}"))["data"].get("name")
        except (httpx.HTTPStatusError, CRMAuthError):
            return None

    async def _notes(self, deal_id: str) -> list[Activity]:
        data = await self._request("GET", "/api/v1/notes", params={
            "deal_id": deal_id, "start": 0, "limit": 100, "sort": "add_time DESC",
        })
        notes = []
        for n in data.get("data") or []:
            when = to_date(n.get("add_time"))
            if when:
                notes.append(Activity(
                    id=f"note-{n['id']}", type="note", date=when, body=clip(strip_html(n.get("content"))),
                    contact_ids=[str(n["person_id"])] if n.get("person_id") else [],
                ))
        return notes

    async def _emails(self, deal_id: str) -> list[Activity]:
        # Reading email needs the mail:read scope. Without it, analyze the deal without emails.
        try:
            data = await self._request("GET", f"/api/v1/deals/{deal_id}/mailMessages", params={
                "start": 0, "limit": 50, "include_body": 1,
            })
        except CRMAuthError:
            return []
        emails = []
        for item in data.get("data") or []:
            m = item.get("data") or {}
            when = to_date(m.get("message_time") or m.get("add_time"))
            if not when or m.get("draft_flag") or m.get("deleted_flag"):
                continue
            parties = (m.get("from") or []) + (m.get("to") or []) + (m.get("cc") or [])
            emails.append(Activity(
                id=f"email-{m['id']}", type="email", date=when,
                direction="outbound" if m.get("sent_flag") else "inbound",
                subject=m.get("subject"),
                body=clip(strip_html(m.get("body")) or m.get("snippet")),
                contact_ids=[str(p["linked_person_id"]) for p in parties if p.get("linked_person_id")],
            ))
        return emails


# -- Mapping Pipedrive records to DealPulse models ---------------------------


def _to_contact(person: dict, decision_maker: str | None) -> Contact:
    name = person.get("name") or "Unknown contact"
    emails = person.get("emails") or []
    email = next((e.get("value") for e in emails if e.get("primary")), None) or (emails[0].get("value") if emails else None)
    # Pipedrive has no buying-role field. A person counts as the decision-maker when the deal's
    # "Decision maker (DealPulse)" field names them, which happens once a rep approves that update.
    is_dm = bool(decision_maker) and name.lower() in decision_maker.lower()
    return Contact(id=str(person["id"]), name=name, title=person.get("job_title"), email=email, is_decision_maker=is_dm)


def _to_activity(a: dict) -> Activity | None:
    when = to_date(a.get("due_date") or a.get("add_time"))
    if when is None or a.get("is_deleted"):
        return None
    kind = ACTIVITY_TYPES.get(a.get("type"), "note")
    body = strip_html(a.get("note")) or a.get("public_description") or ""
    if not a.get("done"):
        body = f"(Scheduled, not done yet) {body}".strip()
    subject = a.get("subject")
    if kind == "note" and a.get("type"):
        subject = f"{a['type'].capitalize()}: {subject}" if subject else a["type"].capitalize()
    people = [a.get("person_id")] + [p.get("person_id") for p in a.get("participants") or []]
    return Activity(
        id=f"activity-{a['id']}", type=kind, date=when, subject=subject, body=clip(body),
        duration_minutes=_minutes(a.get("duration")) if kind in ("call", "meeting") else None,
        contact_ids=[str(p) for p in people if p],
    )


def _minutes(duration: str | None) -> int | None:
    """Pipedrive durations are "HH:MM" (or "HH:MM:SS")."""
    match = re.fullmatch(r"(\d+):(\d{2})(?::\d{2})?", duration or "")
    if not match:
        return None
    minutes = int(match[1]) * 60 + int(match[2])
    return minutes or None


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None
