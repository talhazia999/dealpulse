import React, { useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Divider,
  Flex,
  Heading,
  LoadingSpinner,
  Tag,
  Text,
  hubspot,
} from "@hubspot/ui-extensions";

// The DealPulse API. Must be listed in permittedUrls.fetch in app-hsmeta.json.
const API_URL = "https://api.dealpulse.example";

const SEVERITY_TAG = { high: "error", medium: "warning", low: "default" };
const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 };
const FIELD_LABELS = { next_step: "Next step", decision_maker: "Decision maker", pain_points: "Pain points" };

hubspot.extend(({ context, actions }) => <DealPulseCard context={context} actions={actions} />);

// hubspot.fetch signs each request so the API can check it came from HubSpot for this app.
async function callApi(path, body) {
  const response = await hubspot.fetch(`${API_URL}${path}`, { method: "POST", timeout: 90000, body });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "DealPulse could not complete the request.");
  }
  return data;
}

function DealPulseCard({ context, actions }) {
  const dealId = String(context.crm.objectId);
  const [status, setStatus] = useState("idle"); // idle | analyzing | done | applying | applied
  const [error, setError] = useState(null);
  const [card, setCard] = useState(null);
  const [chosen, setChosen] = useState({});

  const analyze = async () => {
    setStatus("analyzing");
    setError(null);
    try {
      const data = await callApi("/hubspot/card/analyze", { dealId });
      setCard(data);
      setChosen(Object.fromEntries(data.analysis.result.field_updates.map((_, i) => [i, true])));
      setStatus("done");
    } catch (err) {
      setError(err.message);
      setStatus(card ? "done" : "idle");
    }
  };

  const apply = async () => {
    const updates = card.analysis.result.field_updates.filter((_, i) => chosen[i]);
    if (!updates.length) return;
    setStatus("applying");
    setError(null);
    try {
      await callApi("/hubspot/card/apply-updates", { dealId, updates });
      setStatus("applied");
      actions.refreshObjectProperties?.();
      actions.addAlert?.({ type: "success", message: `DealPulse updated ${updates.length} field${updates.length === 1 ? "" : "s"}.` });
    } catch (err) {
      setError(err.message);
      setStatus("done");
    }
  };

  if (status === "analyzing") {
    return <LoadingSpinner label="Reading this deal's emails, notes and calls…" showLabel />;
  }

  if (!card) {
    return (
      <Flex direction="column" gap="sm">
        <Text>Get a summary, risks, the next step and a follow-up email for this deal.</Text>
        {error && <Alert title="Something went wrong" variant="error">{error}</Alert>}
        <Button variant="primary" onClick={analyze}>Analyze deal</Button>
      </Flex>
    );
  }

  const { result } = card.analysis;
  const risks = [...result.risks].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  const email = result.follow_up_email;

  return (
    <Flex direction="column" gap="sm">
      {error && <Alert title="Something went wrong" variant="error">{error}</Alert>}

      <Text>{result.summary}</Text>

      <Divider />
      <Heading>Risks</Heading>
      {risks.length === 0 && <Text>No risks found. This deal looks healthy.</Text>}
      {risks.map((risk, i) => (
        <Flex key={i} direction="column" gap="flush">
          <Flex direction="row" gap="xs" align="center">
            <Tag variant={SEVERITY_TAG[risk.severity]}>{risk.severity}</Tag>
            <Text format={{ fontWeight: "demibold" }}>{risk.title}</Text>
          </Flex>
          {risk.evidence.length > 0 && (
            <Text variant="microcopy">From: {risk.evidence.map((id) => card.evidence_labels[id] || id).join("; ")}</Text>
          )}
        </Flex>
      ))}

      <Divider />
      <Heading>Next step</Heading>
      <Text format={{ fontWeight: "demibold" }}>{result.next_step}</Text>

      <Divider />
      <Heading>Follow-up email</Heading>
      <Text variant="microcopy">
        To: {card.recipient ? `${card.recipient.name}${card.recipient.email ? ` <${card.recipient.email}>` : ""}` : "—"}
      </Text>
      <Text format={{ fontWeight: "demibold" }}>{email.subject}</Text>
      {email.body.split(/\n+/).map((line, i) => <Text key={i}>{line}</Text>)}

      {result.field_updates.length > 0 && (
        <>
          <Divider />
          <Heading>Suggested updates</Heading>
          {result.field_updates.map((update, i) => (
            <Checkbox
              key={i}
              name={`update-${i}`}
              checked={!!chosen[i]}
              readOnly={status === "applied"}
              onChange={(checked) => setChosen({ ...chosen, [i]: checked })}
            >
              {FIELD_LABELS[update.field] || update.field}: {update.value}
            </Checkbox>
          ))}
          {status === "applied" ? (
            <Alert title="Updated" variant="success">The approved fields were written to this deal.</Alert>
          ) : (
            <Button
              variant="primary"
              disabled={status === "applying" || !Object.values(chosen).some(Boolean)}
              onClick={apply}
            >
              {status === "applying" ? "Updating…" : "Approve and update"}
            </Button>
          )}
        </>
      )}

      <Divider />
      <Button variant="secondary" onClick={analyze}>Analyze again</Button>
    </Flex>
  );
}
