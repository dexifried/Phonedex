import { useState } from "react";
import { Network, RefreshCw } from "lucide-react";
import type { BackendConnection as Connection } from "../types";
import { api } from "../api";
import { Alert, Badge, Button, Panel } from "../ui";

const errors: Record<string, string> = {
  authentication_failed: "PhoneDex rejected the operator credential. Review the encrypted provider credential.",
  credentials_unavailable: "The operator credential cannot be unlocked on this computer.",
  invalid_configuration: "Review the PhoneDex control endpoint in Providers & SIP.",
  transport_unavailable: "The control API could not be reached. Your local history is still available.",
  call_index_unavailable: "The optional local outbound call index could not be read.",
};
export default function BackendConnection({ connection, demo, refresh }: {
  connection?: Connection; demo?: boolean; refresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const configured = connection?.mode === "remote-control";
  async function sync() {
    setBusy(true); setError("");
    try {
      const result = await api<Connection>("backend/sync", "POST");
      if (result.status === "offline") setError(errors[result.error || ""] || "PhoneDex synchronization is unavailable. Cached history is preserved.");
      await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <Panel title="PhoneDex backend" icon={<Network size={18} />}>
    {error && <Alert error>{error}</Alert>}
    <dl className="context-list">
      <dt>Connection</dt><dd><Badge tone={connection?.status === "connected" ? "green" : ""}>{connection?.status || "Unconfigured"}</Badge></dd>
      <dt>Runtime</dt><dd>{connection?.provider || "Local PhoneDex core"}</dd>
      <dt>Last successful sync</dt><dd>{connection?.last_success ? new Date(connection.last_success * 1000).toLocaleString() : "No successful sync yet"}</dd>
      <dt>Transcript delivery</dt><dd>{connection?.transcript_delivery || "Opt-in native events"}</dd>
      {configured && <><dt>Local transcript copies</dt><dd>{connection?.local_transcript_cache ? "Enabled" : "Off · temporary display only"}</dd></>}
      <dt>Controls</dt><dd>{connection?.supported_controls?.join(", ") || "Advertised per active call"}</dd>
      {configured && <><dt>Vault disclosure</dt><dd>Unavailable through this protocol</dd></>}
    </dl>
    <p className="panel-description">{configured ? "Uses the existing PhoneDex controller. Carrier connectivity and a connected model session require separate evidence. History stays local after synchronization." : "Select an encrypted PhoneDex control provider, or enable native events in your public core."}</p>
    <Button onClick={sync} disabled={busy || !configured || demo}>
      <RefreshCw size={15} />{busy ? "Synchronizing…" : "Sync backend"}
    </Button>
  </Panel>;
}
