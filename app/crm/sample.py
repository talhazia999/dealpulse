"""A CRM connector backed by the sample deals file. Powers the public demo; updates live in memory."""

import json
from collections import OrderedDict
from pathlib import Path

from app.crm.base import DealNotFound
from app.schemas import Deal, DealSummary, FieldUpdate

SAMPLE_DEALS_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_deals.json"


class SampleCRM:
    name = "sample"

    def __init__(self, path: Path = SAMPLE_DEALS_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._deals = {d["id"]: Deal.model_validate(d) for d in raw}

    async def list_deals(self) -> list[DealSummary]:
        return [DealSummary.model_validate(d.model_dump()) for d in self._deals.values()]

    async def get_deal(self, deal_id: str) -> Deal:
        try:
            return self._deals[deal_id].model_copy(deep=True)
        except KeyError:
            raise DealNotFound(deal_id) from None

    async def update_deal_fields(self, deal_id: str, updates: list[FieldUpdate]) -> Deal:
        deal = await self.get_deal(deal_id)
        updated = deal.model_copy(update={u.field: u.value for u in updates})
        self._deals[deal_id] = updated
        return updated.model_copy(deep=True)


class DemoSessions:
    """One SampleCRM per demo visitor, so one visitor's approved updates never show up for another."""

    def __init__(self, max_sessions: int = 1000):
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, SampleCRM] = OrderedDict()

    def get(self, session_id: str) -> SampleCRM:
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)
        else:
            if len(self._sessions) >= self.max_sessions:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = SampleCRM()
        return self._sessions[session_id]
