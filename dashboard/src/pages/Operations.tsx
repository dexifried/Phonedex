import { useMemo, useState } from "react";
import {
  Activity,
  Download,
  Search,
  Pause,
  Play,
  Terminal,
  MessageCircle,
  ShieldCheck,
  Server,
  Database,
  Sun,
  Moon,
  CheckCircle2,
  LockKeyhole,
  ExternalLink,
  RefreshCw,
  FileText,
} from "lucide-react";
import {
  BarChart,
  Bar,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { Workspace, Item } from "../types";
import type { Guard } from "./Calls";
import { api, download, saveText } from "../api";
import {
  Alert,
  Badge,
  Button,
  Empty,
  PageHeading,
  Panel,
  money,
  duration,
  date,
} from "../ui";
import { ActivityChart, Health } from "./Overview";
import BackendConnection from "./BackendConnection";
export { default as Analytics } from "./Analytics";
export function Logs({ data }: { data: Workspace }) {
  const [query, setQuery] = useState(""),
    [level, setLevel] = useState("all"),
    [component, setComponent] = useState("all"),
    [paused, setPaused] = useState(false),
    [snapshot, setSnapshot] = useState<Item[]>([]);
  const source = paused ? snapshot : data.logs;
  const logs = source.filter(
    (l) =>
      [l.message, l.component, l.source, l.severity, l.level, l.kind]
        .join(" ")
        .toLowerCase()
        .includes(query.toLowerCase()) &&
      (level === "all" ||
        (l.severity || l.level || "info").toLowerCase() === level) &&
      (component === "all" || (l.component || l.source) === component),
  );
  return (
    <>
      <PageHeading
        title="A clear view under the hood."
        description="Sanitized operational events, without credentials or hidden model reasoning."
        action={
          <>
            <Button
              onClick={() => {
                if (!paused) setSnapshot(data.logs);
                setPaused(!paused);
              }}
            >
              {paused ? <Play size={15} /> : <Pause size={15} />}{" "}
              {paused ? "Resume" : "Pause"}
            </Button>
            <Button
              onClick={() =>
                saveText(
                  logs
                    .map(
                      (l) =>
                        `${date(l.at || l.timestamp || l.created_at)} [${l.severity || l.level || "info"}] ${l.component || l.source || "core"}: ${l.message || l.text || ""}`,
                    )
                    .join("\n"),
                  "phonedex-sanitized.log",
                )
              }
            >
              <Download size={15} />
              Download
            </Button>
          </>
        }
      />
      <div className="filter-bar">
        <div className="search-input">
          <Search size={16} />
          <input
            aria-label="Search logs"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search sanitized logs…"
          />
        </div>
        <select
          aria-label="Log severity"
          value={level}
          onChange={(e) => setLevel(e.target.value)}
        >
          <option value="all">All severities</option>
          {["debug", "info", "warning", "error", "critical"].map((l) => (
            <option key={l}>{l}</option>
          ))}
        </select>
        <select
          aria-label="Log component"
          value={component}
          onChange={(e) => setComponent(e.target.value)}
        >
          <option value="all">All components</option>
          {[
            ...new Set(
              data.logs.map((l) => l.component || l.source).filter(Boolean),
            ),
          ].map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
        <Badge tone={paused ? "" : "green"}>
          <span className={`dot ${paused ? "" : "green"}`} />
          {paused ? "Paused" : "Listening"}
        </Badge>
      </div>
      <Panel className="log-panel">
        <div className="log-header">
          <Terminal size={15} />
          <span>workspace / sanitized events</span>
          <small>{logs.length} events</small>
        </div>
        {logs.length ? (
          <div className="log-lines">
            {logs.map((l, i) => (
              <div key={l.id || i}>
                <time>{date(l.at || l.timestamp || l.created_at)}</time>
                <span
                  className={`log-level ${(l.severity || l.level || "info").toLowerCase()}`}
                >
                  {l.severity || l.level || "info"}
                </span>
                <span className="log-component">
                  {l.component || l.source || "core"}
                </span>
                <span>{l.message || l.text || l.kind}</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            icon={<Terminal size={30} />}
            title="All quiet on this line."
            description="Sanitized core and workspace events will appear here."
          />
        )}
      </Panel>
    </>
  );
}
export function Messages() {
  return (
    <>
      <PageHeading
        title="Messages."
        description="A place for future conversations beyond the call."
      />
      <Panel>
        <Empty
          icon={<MessageCircle size={34} />}
          title="Messaging is unavailable"
          description="The current PhoneDex core does not support sending or receiving messages."
          action={<Button disabled>Messaging unavailable</Button>}
        />
      </Panel>
    </>
  );
}
export function Settings({
  data,
  theme,
  setTheme,
  refresh,
}: {
  data: Workspace;
  theme: string;
  setTheme: (v: string) => void;
  refresh: () => Promise<void>;
}) {
  const [tab, setTab] = useState("General"),
    [checked, setChecked] = useState(false);
  return (
    <>
      <PageHeading
        title="Your workspace, your way."
        description="Local settings, connection diagnostics and privacy by design."
        action={
          <Button
            onClick={async () => {
              await refresh();
              setChecked(true);
            }}
          >
            <RefreshCw size={15} />
            Run diagnostics
          </Button>
        }
      />
      <div className="tabs settings-tabs">
        {["General", "Diagnostics", "Privacy", "Installation"].map((t) => (
          <button
            key={t}
            className={tab === t ? "selected" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {checked && (
        <Alert>
          Workspace diagnostics refreshed. Provider validation and carrier
          connectivity require separate checks.
        </Alert>
      )}
      {tab === "General" ? (
        <div className="settings-grid">
          <Panel
            title="Appearance"
            icon={theme === "dark" ? <Moon size={18} /> : <Sun size={18} />}
          >
            <p className="panel-description">
              Choose how your Control Centre feels. Your preference stays on
              this device.
            </p>
            <div className="theme-options">
              {["dark", "light"].map((t) => (
                <button
                  key={t}
                  onClick={() => setTheme(t)}
                  className={theme === t ? "selected" : ""}
                >
                  <div className={`theme-preview ${t}`}>
                    <div />
                    <span />
                    <span />
                    <span />
                  </div>
                  <span>
                    {t === "dark" ? "Midnight" : "Daylight"}
                    {theme === t && <CheckCircle2 size={15} />}
                  </span>
                </button>
              ))}
            </div>
          </Panel>
          <Panel title="Workspace" icon={<Server size={18} />}>
            <dl className="context-list">
              <dt>Connection</dt>
              <dd>{window.location.hostname}</dd>
              <dt>Version</dt>
              <dd>PhoneDex Control Centre v0.1.0</dd>
              <dt>Sessions</dt>
              <dd>HttpOnly, SameSite Strict</dd>
              <dt>Local storage</dt>
              <dd>Theme preference only</dd>
            </dl>
          </Panel>
        </div>
      ) : tab === "Diagnostics" ? (
        <div className="settings-grid">
          <BackendConnection connection={data.health.backend} demo={data.health.demo} refresh={refresh} />
          <Health health={data.health} />
          <Panel title="Runtime capabilities" icon={<ShieldCheck size={18} />}>
            <div className="capability-list">
              {Object.entries(data.capabilities).map(([k, v]) => (
                <div key={k}>
                  <span>{k.replaceAll("_", " ")}</span>
                  <Badge tone={v ? "green" : ""}>
                    {v ? "Supported" : "Unavailable"}
                  </Badge>
                </div>
              ))}
            </div>
            <p className="panel-description">
              Call-specific controls appear only when advertised by the active
              call. The dashboard never starts or restarts the gateway.
            </p>
          </Panel>
        </div>
      ) : tab === "Privacy" ? (
        <div className="settings-grid">
          <Panel title="Private by design" icon={<LockKeyhole size={18} />}>
            <div className="policy-list">
              {[
                "Personal values and provider credentials are encrypted at rest.",
                "Sensitive actions require operator password reauthentication.",
                "Vault disclosures are limited to exact fields, calls, recipients, purposes and expiry.",
                "Configured vault values are redacted before persistence and export.",
                "No raw model reasoning is captured.",
                "Spoken disclosures are irreversible; grants can be revoked for future access.",
              ].map((t) => (
                <p key={t}>
                  <ShieldCheck size={17} />
                  {t}
                </p>
              ))}
            </div>
          </Panel>
          <Panel title="Transcript retention" icon={<Database size={18} />}>
            <p className="panel-description">
              {data.health.backend?.mode === "remote-control"
                ? "The control API supplies existing provider transcript batches. Temporary display uses memory. Saving local copies requires the provider's explicit transcript-cache option; disabling it does not delete earlier copies."
                : "Transcript recording is opt-in and controlled by the PhoneDex core configuration. The dashboard displays only completed utterances that the core records."}
            </p>
            <Alert>
              Review your core transcript storage settings before recording
              conversations.
            </Alert>
          </Panel>
        </div>
      ) : (
        <Panel
          title="Run your local Control Centre"
          icon={<FileText size={18} />}
        >
          <div className="installation">
            <p>
              Install the optional dashboard dependencies in your public
              PhoneDex checkout, then launch the local management service.
            </p>
            <code>python phonedex.py dashboard</code>
            <p>
              Keep the terminal open while you use the dashboard. Use the
              headless option when you prefer to open the loopback address
              yourself.
            </p>
            <code>python phonedex.py dashboard --headless</code>
            <p>
              The dashboard does not copy private configuration or connect a
              carrier automatically. Configure providers, validate locally,
              allowlist a contact, and explicitly review each call.
            </p>
          </div>
        </Panel>
      )}
    </>
  );
}
