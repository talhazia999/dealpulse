"""HubSpot connector: reads a deal with its contacts, emails, notes, calls and meetings,
and writes rep-approved fields back. Uses the HubSpot CRM REST API with an OAuth access token."""

import asyncio
from datetime import date

import httpx

from app.crm.base import CRMAuthError, DealNotFound
from app.crm.http import shared_http
from app.crm.text import MAX_ACTIVITIES, clip, strip_html, to_date, to_datetime, to_number
from app.schemas import Activity, Contact, Deal, DealSummary, FieldUpdate

API_BASE = "https://api.hubapi.com"

# Where each updatable field lives in HubSpot. next_step is a built-in deal property;
# the other two are custom properties DealPulse creates on install (see ensure_properties).
FIELD_PROPERTIES = {
    "next_step": "hs_next_step",
    "decision_maker": "dealpulse_decision_maker",
    "pain_points": "dealpulse_pain_points",
}
CUSTOM_PROPERTIES = [
    {"name": "dealpulse_decision_maker", "label": "Decision maker (DealPulse)"},
    {"name": "dealpulse_pain_points", "label": "Pain points (DealPulse)"},
]

DEAL_PROPERTIES = [
    "dealname", "amount", "deal_currency_code", "dealstage", "pipeline",
    "closedate", "createdate", "hubspot_owner_id", *FIELD_PROPERTIES.values(),
]
CONTACT_PROPERTIES = ["firstname", "lastname", "jobtitle", "email", "hs_buying_role"]
ACTIVITY_PROPERTIES = {
    "emails": ["hs_timestamp", "hs_email_subject", "hs_email_text", "hs_email_html", "hs_email_direction"],
    "notes": ["hs_timestamp", "hs_note_body"],
    "calls": ["hs_timestamp", "hs_call_title", "hs_call_body", "hs_call_duration"],
    "meetings": ["hs_timestamp", "hs_meeting_title", "hs_meeting_body", "hs_meeting_start_time", "hs_meeting_end_time"],
}

class HubSpotCRM:
    name = "hubspot"

    def __init__(self, access_token: str, http: httpx.AsyncClient | None = None):
        self._http = http or shared_http()
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._stage_labels: dict[str, str] | None = None

    # -- CRMConnector ------------------------------------------------------

    async def list_deals(self) -> list[DealSummary]:
        body = {
            "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "DESCENDING"}],
            "properties": DEAL_PROPERTIES,
            "limit": 20,
        }
        data = await self._request("POST", "/crm/v3/objects/deals/search", json=body)
        labels = await self._stage_labels_by_id()
        return [
            DealSummary(
                id=d["id"],
                name=d["properties"].get("dealname") or "Untitled deal",
                company=d["properties"].get("dealname") or "",
                amount=to_number(d["properties"].get("amount")),
                currency=d["properties"].get("deal_currency_code") or "USD",
                stage=labels.get(d["properties"].get("dealstage") or "", d["properties"].get("dealstage") or ""),
            )
            for d in data.get("results", [])
        ]

    async def get_deal(self, deal_id: str) -> Deal:
        record = await self._request(
            "GET",
            f"/crm/v3/objects/deals/{deal_id}",
            params={
                "properties": ",".join(DEAL_PROPERTIES),
                "propertiesWithHistory": "dealstage",
                "associations": "contacts,companies,emails,notes,calls,meetings",
            },
            not_found=deal_id,
        )
        props = record["properties"]
        assoc = {kind: [r["id"] for r in value.get("results", [])] for kind, value in record.get("associations", {}).items()}

        company_task = self._company_name(assoc.get("companies", []))
        owner_task = self._owner_name(props.get("hubspot_owner_id"))
        contacts_task = self._contacts(assoc.get("contacts", []))
        activity_tasks = [self._activities(kind, assoc.get(kind, [])) for kind in ACTIVITY_PROPERTIES]
        company, owner, contacts, *activity_groups = await asyncio.gather(
            company_task, owner_task, contacts_task, *activity_tasks
        )

        deal_contact_ids = {c.id for c in contacts}
        activities = sorted((a for group in activity_groups for a in group), key=lambda a: a.date, reverse=True)
        activities = [
            a.model_copy(update={"contact_ids": [c for c in a.contact_ids if c in deal_contact_ids]})
            for a in activities[:MAX_ACTIVITIES]
        ]

        labels = await self._stage_labels_by_id()
        stage_id = props.get("dealstage") or ""
        created = to_date(props.get("createdate")) or date.today()
        return Deal(
            id=record["id"],
            name=props.get("dealname") or "Untitled deal",
            company=company or props.get("dealname") or "Unknown company",
            amount=to_number(props.get("amount")),
            currency=props.get("deal_currency_code") or "USD",
            stage=labels.get(stage_id, stage_id or "Unknown"),
            stage_entered_date=_stage_entered(record, stage_id) or created,
            close_date=to_date(props.get("closedate")),
            owner_name=owner,
            next_step=props.get(FIELD_PROPERTIES["next_step"]) or None,
            decision_maker=props.get(FIELD_PROPERTIES["decision_maker"]) or None,
            pain_points=props.get(FIELD_PROPERTIES["pain_points"]) or None,
            contacts=contacts,
            activities=activities,
        )

    async def update_deal_fields(self, deal_id: str, updates: list[FieldUpdate]) -> Deal:
        properties = {FIELD_PROPERTIES[u.field]: u.value for u in updates}
        await self._request("PATCH", f"/crm/v3/objects/deals/{deal_id}", json={"properties": properties}, not_found=deal_id)
        return await self.get_deal(deal_id)

    # -- Setup ---------------------------------------------------------------

    async def ensure_properties(self) -> None:
        """Create DealPulse's custom deal properties. Safe to call on every install."""
        for prop in CUSTOM_PROPERTIES:
            body = {**prop, "groupName": "dealinformation", "type": "string", "fieldType": "textarea"}
            try:
                await self._request("POST", "/crm/v3/properties/deals", json=body)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:  # 409: it already exists
                    raise

    # -- Helpers -------------------------------------------------------------

    async def _request(self, method: str, path: str, *, not_found: str | None = None, **kwargs) -> dict:
        for attempt in range(2):
            response = await self._http.request(method, API_BASE + path, headers=self._headers, **kwargs)
            if response.status_code == 429 and attempt == 0:
                await asyncio.sleep(min(float(response.headers.get("Retry-After", 1)), 10))
                continue
            break
        if response.status_code == 404 and not_found is not None:
            raise DealNotFound(not_found)
        if response.status_code in (401, 403):
            raise CRMAuthError(f"HubSpot rejected the request ({response.status_code}).")
        response.raise_for_status()
        return response.json() if response.content else {}

    async def _batch_read(self, object_type: str, ids: list[str], properties: list[str]) -> list[dict]:
        if not ids:
            return []
        results = []
        for start in range(0, len(ids), 100):
            body = {"properties": properties, "inputs": [{"id": i} for i in ids[start:start + 100]]}
            data = await self._request("POST", f"/crm/v3/objects/{object_type}/batch/read", json=body)
            results.extend(data.get("results", []))
        return results

    async def _associated_contacts(self, object_type: str, ids: list[str]) -> dict[str, list[str]]:
        if not ids:
            return {}
        body = {"inputs": [{"id": i} for i in ids[:1000]]}
        data = await self._request("POST", f"/crm/v4/associations/{object_type}/contacts/batch/read", json=body)
        return {
            str(r["from"]["id"]): [str(t["toObjectId"]) for t in r.get("to", [])]
            for r in data.get("results", [])
        }

    async def _stage_labels_by_id(self) -> dict[str, str]:
        if self._stage_labels is None:
            data = await self._request("GET", "/crm/v3/pipelines/deals")
            self._stage_labels = {
                stage["id"]: stage["label"]
                for pipeline in data.get("results", [])
                for stage in pipeline.get("stages", [])
            }
        return self._stage_labels

    async def _company_name(self, company_ids: list[str]) -> str | None:
        companies = await self._batch_read("companies", company_ids[:1], ["name"])
        return companies[0]["properties"].get("name") if companies else None

    async def _owner_name(self, owner_id: str | None) -> str:
        if not owner_id:
            return "Unassigned"
        try:
            owner = await self._request("GET", f"/crm/v3/owners/{owner_id}")
        except (httpx.HTTPStatusError, CRMAuthError):
            return "Unknown owner"
        return " ".join(filter(None, [owner.get("firstName"), owner.get("lastName")])) or owner.get("email") or "Unknown owner"

    async def _contacts(self, contact_ids: list[str]) -> list[Contact]:
        records = await self._batch_read("contacts", contact_ids, CONTACT_PROPERTIES)
        contacts = []
        for r in records:
            p = r["properties"]
            name = " ".join(filter(None, [p.get("firstname"), p.get("lastname")])) or p.get("email") or "Unknown contact"
            # Buying role is a multi-select contact property; HubSpot stores multiple values separated by ";".
            roles = {role.strip().upper() for role in (p.get("hs_buying_role") or "").split(";")}
            contacts.append(Contact(
                id=r["id"], name=name, title=p.get("jobtitle"), email=p.get("email"),
                is_decision_maker="DECISION_MAKER" in roles,
            ))
        return contacts

    async def _activities(self, kind: str, ids: list[str]) -> list[Activity]:
        records, contacts_by_activity = await asyncio.gather(
            self._batch_read(kind, ids, ACTIVITY_PROPERTIES[kind]),
            self._associated_contacts(kind, ids),
        )
        activities = []
        for r in records:
            activity = _to_activity(kind, r["id"], r["properties"])
            if activity:
                activities.append(activity.model_copy(update={"contact_ids": contacts_by_activity.get(r["id"], [])}))
        return activities


# -- Mapping HubSpot records to DealPulse models ----------------------------


def _to_activity(kind: str, activity_id: str, p: dict) -> Activity | None:
    when = to_date(p.get("hs_timestamp") or p.get("hs_meeting_start_time"))
    if when is None:
        return None
    if kind == "emails":
        body = p.get("hs_email_text") or strip_html(p.get("hs_email_html"))
        direction = "inbound" if p.get("hs_email_direction") == "INCOMING_EMAIL" else "outbound"
        return Activity(id=activity_id, type="email", date=when, direction=direction,
                        subject=p.get("hs_email_subject"), body=clip(body))
    if kind == "notes":
        return Activity(id=activity_id, type="note", date=when, body=clip(strip_html(p.get("hs_note_body"))))
    if kind == "calls":
        duration_ms = to_number(p.get("hs_call_duration"))
        return Activity(id=activity_id, type="call", date=when, subject=p.get("hs_call_title"),
                        body=clip(strip_html(p.get("hs_call_body"))),
                        duration_minutes=round(duration_ms / 60000) if duration_ms else None)
    start, end = to_datetime(p.get("hs_meeting_start_time")), to_datetime(p.get("hs_meeting_end_time"))
    minutes = round((end - start).total_seconds() / 60) if start and end else None
    return Activity(id=activity_id, type="meeting", date=when, subject=p.get("hs_meeting_title"),
                    body=clip(strip_html(p.get("hs_meeting_body"))), duration_minutes=minutes)


def _stage_entered(record: dict, stage_id: str) -> date | None:
    """The date the deal moved into its current stage, from the dealstage property history (newest first)."""
    for entry in record.get("propertiesWithHistory", {}).get("dealstage", []):
        if entry.get("value") == stage_id:
            return to_date(entry.get("timestamp"))
    return None
