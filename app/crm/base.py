"""The contract every CRM connector implements. The analyzer never talks to a CRM directly,
so adding HubSpot, Pipedrive or Salesforce means writing one class like SampleCRM."""

from typing import Protocol

from app.schemas import Deal, DealSummary, FieldUpdate


class DealNotFound(Exception):
    pass


class CRMConnector(Protocol):
    name: str

    async def list_deals(self) -> list[DealSummary]: ...

    async def get_deal(self, deal_id: str) -> Deal:
        """Return the deal with its contacts and activities, or raise DealNotFound."""
        ...

    async def update_deal_fields(self, deal_id: str, updates: list[FieldUpdate]) -> Deal:
        """Write rep-approved field values back to the CRM and return the updated deal."""
        ...
