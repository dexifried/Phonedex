import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Contact } from "../types";
import { Alert, Button, Panel, date, duration } from "../ui";

type ProviderCall = {
  id: string;
  direction: "inbound" | "outbound";
  number: string | null;
  started_at: string;
  duration_seconds: number | null;
  status: string;
  has_snippet: boolean;
};
type ProviderLine = { speaker: string; text: string };

export default function AgentPhoneHistory({ contacts }: { contacts: Contact[] }) {
  const [calls, setCalls] = useState<ProviderCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<ProviderLine[]>([]);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState("");
  const requestId = useRef(0);

  async function reload() {
    setLoading(true);
    setError("");
    try {
      const response = await api<{ calls: ProviderCall[] }>("agentphone/calls");
      setCalls(response.calls);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function inspect(call: ProviderCall) {
    if (selected === call.id) {
      ++requestId.current;
      setTranscriptLoading(false);
      setSelected(null);
      return;
    }
    const request = ++requestId.current;
    setSelected(call.id);
    setTranscript([]);
    setTranscriptError("");
    setTranscriptLoading(true);
    try {
      const result = await api<{ transcript: ProviderLine[] }>(
        `agentphone/calls/${encodeURIComponent(call.id)}/transcript`
      );
      if (request === requestId.current) setTranscript(result.transcript);
    } catch (e) {
      if (request === requestId.current) setTranscriptError((e as Error).message);
    } finally {
      if (request === requestId.current) setTranscriptLoading(false);
    }
  }

  return (
    <Panel title="AgentPhone carrier history">
      <p className="muted">
        Read-only provider records. Times are shown in your device's local time.
        These may overlap PhoneDex control records; calls are never matched by number suffix.
        Caller ID is provider-reported, not identity verification.
      </p>
      <div className="row" style={{ justifyContent: "space-between", margin: "12px 0" }}>
        <strong>{loading ? "Loading callsΓÇª" : `${calls.length} recent provider calls`}</strong>
        <Button onClick={() => void reload()}>Refresh carrier history</Button>
      </div>
      {error && <Alert error>{error}</Alert>}
      {!loading && !error && calls.length === 0 && <p>No provider calls were returned.</p>}
      <div style={{ display: "grid", gap: 12 }}>
        {calls.map((call) => (
          <div className="panel" key={call.id} style={{ padding: 14 }}>
            <button
              type="button"
              className="text-button"
              aria-expanded={selected === call.id}
              onClick={() => void inspect(call)}
              style={{ width: "100%", textAlign: "left", display: "grid", gap: 5 }}
            >
              <strong>{call.direction === "inbound" ? "From" : "To"} {contacts.find((contact) => contact.number === call.number)?.name || call.number || "Unknown / private number"}</strong>
              {contacts.some((contact) => contact.number === call.number) && <small>{call.number}</small>}
              <span>{date(call.started_at)} ┬╖ {call.direction} ┬╖ {duration(call.duration_seconds)} ┬╖ {call.status}</span>
              <span className="cyan">
                {selected === call.id ? "Hide conversation" : "View available conversation"}
              </span>
            </button>
            {selected === call.id && (
              <div style={{ marginTop: 14, display: "grid", gap: 12 }}>
                <small>Provider transcripts are model-generated estimates, not audio recordings.</small>
                {transcriptLoading && <p>Loading conversationΓÇª</p>}
                {transcriptError && <Alert error>{transcriptError}</Alert>}
                {!transcriptLoading && !transcriptError && transcript.length === 0 && (
                  <p>No transcript available for this call.</p>
                )}
                {transcript.map((line, index) => (
                  <div key={index} style={{ borderLeft: "2px solid var(--border)", paddingLeft: 12 }}>
                    <strong>{line.speaker}</strong>
                    <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{line.text}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </Panel>
  );
}
