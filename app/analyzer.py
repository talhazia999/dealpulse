"""Runs the AI deal analysis: one structured-output call to Claude, then checks on the result."""

import anthropic

from app import config
from app.prompts import SYSTEM_PROMPT, build_user_prompt
from app.schemas import AnalysisResult, Deal, DealAnalysis
from app.signals import compute_signals

DEAL_FIELDS_EVIDENCE = "deal_fields"


class AnalysisError(Exception):
    """The model returned no usable analysis (refusal, truncation or unparseable output)."""


class DealAnalyzer:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        model: str = config.ANTHROPIC_MODEL,
        effort: str = config.ANTHROPIC_EFFORT,
    ):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = model
        self.effort = effort

    async def analyze(self, deal: Deal) -> DealAnalysis:
        signals = compute_signals(deal)
        response = await self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(deal, signals)}],
            output_format=AnalysisResult,
            output_config={"effort": self.effort},
            # If a safety classifier declines, the API retries on a suitable fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

        if response.stop_reason == "refusal":
            raise AnalysisError("The model declined to analyze this deal.")
        if response.stop_reason == "max_tokens":
            raise AnalysisError("The analysis was cut off before it finished.")
        if response.parsed_output is None:
            raise AnalysisError("The model returned an analysis that did not match the schema.")

        return DealAnalysis(
            deal_id=deal.id,
            model=response.model,
            signals=signals,
            result=clean_result(response.parsed_output, deal),
        )


def clean_result(result: AnalysisResult, deal: Deal) -> AnalysisResult:
    """Keep the output grounded in the deal: drop evidence IDs and contacts that do not exist."""
    valid_evidence = {a.id for a in deal.activities} | {DEAL_FIELDS_EVIDENCE}
    risks = [
        risk.model_copy(update={"evidence": [e for e in risk.evidence if e in valid_evidence]})
        for risk in result.risks
    ]

    email = result.follow_up_email
    contact_ids = [c.id for c in deal.contacts]
    if contact_ids and email.to_contact_id not in contact_ids:
        email = email.model_copy(update={"to_contact_id": contact_ids[0]})

    return result.model_copy(update={"risks": risks, "follow_up_email": email})
