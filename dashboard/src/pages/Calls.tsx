import { useEffect, useRef, useState } from "react";
import {
  Phone,
  Search,
  Download,
  Pause,
  Play,
  Send,
  PhoneOff,
  Clock,
  CheckCheck,
  FileText,
  ArrowLeft,
  ShieldCheck,
} from "lucide-react";
import { api, download } from "../api";
import type { Call, Item, Workspace } from "../types";
import {
  Alert,
  Badge,
  Button,
  Empty,
  Field,
  Modal,
  PageHeading,
  Panel,
  date,
  duration,
  money,
  useAutoScroll,
} from "../ui";
import { CallsTable } from "./Overview";
export type Guard = (action: () => Promise<void>, reason: string) => void;
export function CallsPage({
  data,
  live = false,
  transcripts = false,
  onCall,
  search = "",
  prepare,
}: {
  data: Workspace;
  live?: boolean;
  transcripts?: boolean;
  onCall: (c: Call) => void;
  search?: string;
  prepare: () => void;
}) {
  const [query, setQuery] = useState(search),
    [state, setState] = useState("all"),
    [direction, setDirection] = useState("all"),
    [agent, setAgent] = useState("all"),
    [from, setFrom] = useState("");
  const calls = data.calls.filter(
    (c) =>
      (!live ||
        [
          "active",
          "dialing",
          "ringing",
          "connected",
          "answered",
          "ending",
          "in_progress",
        ].includes(c.state)) &&
      (!transcripts || c.transcript?.length) &&
      [
        c.contact,
        c.number_masked,
        c.agent_name,
        c.outcome,
        ...(c.transcript || []).map((t) => t.text),
      ]
        .join(" ")
        .toLowerCase()
        .includes(query.toLowerCase()) &&
      (state === "all" || c.state === state) &&
      (direction === "all" || c.direction === direction) &&
      (agent === "all" || c.agent_id === agent) &&
      (!from || (c.started_at ?? "") >= from),
  );
  return (
    <>
      <PageHeading
        title={
          live
            ? "Every conversation. In real time."
            : transcripts
              ? "The conversation, preserved."
              : "Your call history."
        }
        description={
          live
            ? "Monitor active sessions and guide your agents as they work."
            : transcripts
              ? "Search completed utterances and open the full conversation."
              : "Explore recorded calls, outcomes and structured task results."
        }
        action={
          <Button variant="primary" onClick={prepare}>
            <Phone size={16} />
            Prepare a call
          </Button>
        }
      />
      <div className="filter-bar">
        <div className="search-input">
          <Search size={16} />
          <input
            aria-label="Search calls"
            placeholder={
              transcripts
                ? "Search conversation text…"
                : "Search contacts, agents, outcomes…"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <select
          aria-label="Call state"
          value={state}
          onChange={(e) => setState(e.target.value)}
        >
          <option value="all">All states</option>
          {[...new Set(data.calls.map((c) => c.state))].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <select
          aria-label="Direction"
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
        >
          <option value="all">All directions</option>
          <option>outbound</option>
          <option>inbound</option>
        </select>
        <select
          aria-label="Agent"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
        >
          <option value="all">All agents</option>
          {data.agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <input
          aria-label="Calls after date"
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
        />
      </div>
      {live ? (
        calls.length ? (
          <div className="card-grid">
            {calls.map((c) => (
              <button
                className="call-card panel"
                key={c.id}
                onClick={() => onCall(c)}
              >
                <div className="row">
                  <div className="stat-icon cyan">
                    <Phone size={20} />
                  </div>
                  <Badge tone="green">
                    <span className="dot green" />
                    {c.state}
                  </Badge>
                </div>
                <h2>{c.contact || c.number_masked}</h2>
                <p>
                  {c.agent_name} · {c.direction}
                </p>
                <div className="row">
                  <span>
                    <Clock size={13} /> {duration(c.duration_seconds)}
                  </span>
                  <span className="cyan">Open monitor →</span>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <Panel>
            <Empty
              icon={<Phone size={32} />}
              title="A quiet line. Everything is ready."
              description="Active calls appear here when an instrumented PhoneDex gateway is connected."
              action={<Button onClick={prepare}>Prepare a call</Button>}
            />
          </Panel>
        )
      ) : (
        <Panel>
          <CallsTable calls={calls} onCall={onCall} />
        </Panel>
      )}
    </>
  );
}
function StructuredResult({
  value,
  label = "Task result",
}: {
  value: any;
  label?: string;
}) {
  if (value == null) return null;
  if (Array.isArray(value))
    return (
      <>
        {value.map((v, i) => (
          <StructuredResult
            key={i}
            value={v}
            label={v.label || v.type || `Result ${i + 1}`}
          />
        ))}
      </>
    );
  if (typeof value !== "object")
    return (
      <div className="result-card">
        <small>{label.replaceAll("_", " ")}</small>
        <strong>{String(value)}</strong>
      </div>
    );
  return (
    <div className="result-card">
      <h3>
        <CheckCheck size={16} />
        {value.title || label}
      </h3>
      {Object.entries(value)
        .filter(([k]) => k !== "title")
        .map(([k, v]) =>
          typeof v === "object" ? (
            <StructuredResult key={k} value={v} label={k} />
          ) : (
            <div className="result-pair" key={k}>
              <span>
                {k === "source_event_id"
                  ? "Transcript evidence"
                  : k.replaceAll("_", " ")}
              </span>
              <strong>{v == null ? "Not recorded" : String(v)}</strong>
            </div>
          ),
        )}
    </div>
  );
}
export function CallDetail({
  call,
  back,
  guard,
  refresh,
}: {
  call: Call;
  back: () => void;
  guard: Guard;
  refresh: () => void;
}) {
  const [tab, setTab] = useState("Conversation"),
    [query, setQuery] = useState(""),
    [paused, setPaused] = useState(false),
    [instruction, setInstruction] = useState(""),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);
  useAutoScroll(transcriptRef, call.transcript?.length, paused);
  const active = [
    "active",
    "dialing",
    "ringing",
    "connected",
    "answered",
    "ending",
    "in_progress",
  ].includes(call.state);
  const supported = (kind: string) =>
    active &&
    (Array.isArray(call.controls)
      ? call.controls.includes(kind) ||
        (kind === "private_instruction" && call.controls.includes("steer"))
      : Boolean(call.controls?.[kind]));
  async function control(kind: string, payload: Item = {}) {
    setError("");
    try {
      await api(`calls/${call.id}/control`, "POST", {
        kind,
        payload,
        confirm: true,
      });
      setMessage(
        kind === "hangup"
          ? "Hangup requested. Waiting for the core to acknowledge."
          : "Private instruction queued for the agent.",
      );
      setInstruction("");
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <button className="text-button back-button" onClick={back}>
        <ArrowLeft size={15} /> Back to calls
      </button>
      <PageHeading
        eyebrow={`${call.direction || "Call"} · ${call.id}`}
        title={call.contact || "Call detail"}
        description={`${call.number_masked || "Number unavailable"} · ${call.agent_name || "No agent recorded"} · ${date(call.started_at)}`}
        action={
          <>
            <Badge tone={active ? "green" : ""}>{call.state}</Badge>
            <Button
              onClick={() =>
                guard(async () => {
                  await download(
                    `calls/${encodeURIComponent(call.id)}/export`,
                    `phonedex-${call.id}.json`,
                  );
                }, "Export this transcript")
              }
            >
              <Download size={15} />
              Export
            </Button>
          </>
        }
      />
      <div className="detail-stats">
        {[
          ["Duration", duration(call.duration_seconds)],
          ["Task outcome", call.outcome || "Not recorded"],
          ["Cost", money(call.cost)],
          ["Provider", call.provider || "Unknown"],
        ].map(([k, v]) => (
          <div key={k}>
            <small>{k}</small>
            <strong>{v}</strong>
          </div>
        ))}
      </div>
      {error && <Alert error>{error}</Alert>}
      {message && <Alert>{message}</Alert>}
      <div className="call-layout">
        <Panel
          title="Call monitor"
          icon={<ActivityIcon />}
          action={
            <Badge tone={active ? "green" : ""}>
              {active ? "Live session" : "Recorded session"}
            </Badge>
          }
        >
          <div className="tabs">
            {["Conversation", "Timeline", "Results"].map((t) => (
              <button
                key={t}
                className={tab === t ? "selected" : ""}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </div>
          {tab === "Conversation" ? (
            <>
              <div className="transcript-toolbar">
                <div className="search-input">
                  <Search size={15} />
                  <input
                    aria-label="Search transcript"
                    placeholder="Search this conversation…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </div>
                <Button
                  onClick={() => setPaused(!paused)}
                  title="Toggle auto-scroll"
                >
                  {paused ? <Play size={14} /> : <Pause size={14} />}{" "}
                  {paused ? "Resume" : "Pause"}
                </Button>
              </div>
              <div className="transcript" ref={transcriptRef}>
                {(call.transcript || [])
                  .filter((t) =>
                    t.text.toLowerCase().includes(query.toLowerCase()),
                  )
                  .map((t, i) => (
                    <div
                      className={`utterance ${["agent", "assistant"].includes(t.speaker) ? "agent" : ""}`}
                      key={t.event_id || t.id || i}
                    >
                      <div>
                        <strong>{t.speaker}</strong>
                        <small>
                          {t.ms != null
                            ? duration(t.ms / 1000)
                            : date(t.at || t.timestamp)}
                        </small>
                      </div>
                      <p>{t.text}</p>
                    </div>
                  ))}
                {!call.transcript?.length && (
                  <Empty
                    icon={<FileText size={28} />}
                    title="No transcript recorded"
                    description="Only completed utterances are shown. Transcript storage must be enabled in the core."
                  />
                )}
              </div>
            </>
          ) : tab === "Timeline" ? (
            <div className="timeline">
              {(call.timeline || []).map((e, i) => (
                <div key={e.id || i}>
                  <span className="activity-dot" />
                  <small>
                    {e.ms != null
                      ? duration(e.ms / 1000)
                      : date(e.at || e.timestamp || e.created_at)}
                  </small>
                  <div>
                    <strong>{e.title || e.kind || e.type || "Event"}</strong>
                    <p>{e.text || e.message || e.description || ""}</p>
                    {e.tool && <Badge>{e.tool}</Badge>}
                  </div>
                </div>
              ))}
              {!call.timeline?.length && (
                <Empty
                  title="No timeline events"
                  description="Sanitized lifecycle and tool events will appear here."
                />
              )}
            </div>
          ) : (
            <div className="results">
              <StructuredResult value={call.results} />
              {(!call.results || !Object.keys(call.results).length) && (
                <Empty
                  icon={<CheckCheck size={28} />}
                  title="No task result recorded"
                  description="Connection completion does not imply task success."
                />
              )}
              <p className="subtle">
                <ShieldCheck size={14} /> Results come from recorded core
                events. Missing values remain unknown.
              </p>
            </div>
          )}
        </Panel>
        <div className="detail-rail">
          <Panel title="Session controls">
            <p className="panel-description">
              Controls are available only when this call advertises support.
            </p>
            <Field label="Private agent instruction">
              <textarea
                disabled={!supported("private_instruction")}
                placeholder="Guide the agent privately…"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                rows={4}
              />
            </Field>
            <Button
              className="full"
              disabled={
                !supported("private_instruction") || !instruction.trim()
              }
              onClick={() =>
                guard(
                  () => control("private_instruction", { instruction }),
                  "Send a private instruction to this call",
                )
              }
            >
              <Send size={15} />
              Send instruction
            </Button>
            <div className="control-grid">
              {["Hold", "Mute", "Transfer", "Conference"].map((t) => (
                <Button key={t} disabled title="Not supported by the core">
                  {t}
                </Button>
              ))}
            </div>
            <Button
              className="full"
              variant="danger"
              disabled={!supported("hangup")}
              onClick={() =>
                guard(() => control("hangup"), "End this active call")
              }
            >
              <PhoneOff size={16} />
              Hang up
            </Button>
          </Panel>
          <Panel title="Call context">
            <dl className="context-list">
              <dt>Task success</dt>
              <dd>
                {call.task_success == null
                  ? "Unknown"
                  : call.task_success
                    ? "Confirmed"
                    : "Not successful"}
              </dd>
              <dt>Cost provenance</dt>
              <dd>
                {call.cost == null
                  ? "Not reported"
                  : call.cost_kind || "Recorded"}
              </dd>
              <dt>Disclosure scope</dt>
              <dd>Exact recipient and explicit field grants only</dd>
            </dl>
          </Panel>
        </div>
      </div>
    </>
  );
}
function ActivityIcon() {
  return <Phone size={17} className="cyan" />;
}
export function PrepareCall({
  data,
  onClose,
  guard,
  refresh,
}: {
  data: Workspace;
  onClose: () => void;
  guard: Guard;
  refresh: () => void;
}) {
  const [form, setForm] = useState({
      contact_id: "",
      agent_id: "",
      prompt: "",
      duration_seconds: 120,
    }),
    [review, setReview] = useState<Item | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [profiles, setProfiles] = useState<Item[]>([]),
    [fields, setFields] = useState<Item[]>([]),
    [profile, setProfile] = useState(""),
    [selected, setSelected] = useState<string[]>([]),[purpose,setPurpose]=useState('');
  useEffect(() => {
    api("vault/profiles")
      .then((v) => setProfiles(Array.isArray(v) ? v : v.profiles || []))
      .catch(() => {});
  }, []);
  useEffect(() => {
    setSelected([]);
    if (profile)
      api(`vault/profiles/${profile}/fields`)
        .then((v) => setFields(Array.isArray(v) ? v : v.fields || []))
        .catch((e) => setError(e.message));
    else setFields([]);
  }, [profile]);
  async function preflight(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      setReview(await api("preflight", "POST", {...form,profile_fields:selected.map(field_id=>({field_id,purpose}))}));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const contact = data.contacts.find((c) => c.id === form.contact_id);
  return (
    <Modal
      title={review ? "Review your call" : "Prepare a call"}
      onClose={onClose}
      wide
    >
      {error && <Alert error>{error}</Alert>}
      {review ? (
        <>
          <Alert>
            Review the recipient, instructions and duration before placing an
            outbound call. Dialing requires your password.
          </Alert>
          <dl className="review-list">
            <dt>Recipient</dt>
            <dd>
              {contact?.name} · {contact?.number}
            </dd>
            <dt>Agent</dt>
            <dd>{data.agents.find((a) => a.id === form.agent_id)?.name}</dd>
            <dt>Maximum duration</dt>
            <dd>{form.duration_seconds} seconds</dd>
            <dt>Task</dt>
            <dd>{form.prompt || "Agent default prompt"}</dd>
            <dt>Personal data</dt>
            <dd>
              {selected.length
                ? `${selected.length} exact field grants will be created for this call and purpose: ${purpose}. Values remain encrypted until requested.`
                : "No vault fields selected"}
            </dd>
          </dl>
          {review.warnings?.map((w: string, i: number) => (
            <Alert key={i}>{w}</Alert>
          ))}
          <div className="modal-actions">
            <Button onClick={() => setReview(null)}>Back</Button>
            <Button
              variant="primary"
              onClick={() =>
                guard(async () => {
                  try {
                    await api("dial", "POST", {
                      approval_id: review.approval_id,
                      confirm: true,
                    });
                    refresh();
                    onClose();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }, "Place this outbound call")
              }
            >
              <Phone size={16} />
              Confirm & dial
            </Button>
          </div>
        </>
      ) : (
        <form onSubmit={preflight}>
          <div className="form-grid">
            <Field
              label="Contact"
              hint="Only explicitly allowlisted contacts can be dialed."
            >
              <select
                required
                value={form.contact_id}
                onChange={(e) =>
                  setForm({ ...form, contact_id: e.target.value })
                }
              >
                <option value="">Choose a contact</option>
                {data.contacts.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {!c.allowed ? " · not allowlisted" : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Agent">
              <select
                required
                value={form.agent_id}
                onChange={(e) => setForm({ ...form, agent_id: e.target.value })}
              >
                <option value="">Choose an agent</option>
                {data.agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field
            label="What should the agent do?"
            hint="Keep secrets out of prompts. Use scoped vault grants for personal information."
          >
            <textarea
              rows={4}
              value={form.prompt}
              onChange={(e) => setForm({ ...form, prompt: e.target.value })}
            />
          </Field>
          <Field label="Maximum duration (seconds)">
            <input
              required
              type="number"
              min={20}
              max={300}
              value={form.duration_seconds}
              onChange={(e) =>
                setForm({ ...form, duration_seconds: Number(e.target.value) })
              }
            />
          </Field>
          <Field label="Personal profile (optional)">
            <select
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
            >
              <option value="">No personal profile</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          {fields.map((f) => (
            <label className="check" key={f.id}>
              <input
                type="checkbox"
                checked={selected.includes(f.id)}
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? [...selected, f.id]
                      : selected.filter((id) => id !== f.id),
                  )
                }
              />
              {f.label} <small>masked · requires exact-call grant</small>
            </label>
          ))}
          {selected.length>0&&<Field label="Exact disclosure purpose" hint="Applies only to the selected fields and this recipient for this call."><input required maxLength={500} value={purpose} onChange={e=>setPurpose(e.target.value)}/></Field>}
          <div className="modal-actions">
            <Button type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" disabled={busy}>
              {busy ? "Checking…" : "Run preflight"}
              <ShieldCheck size={16} />
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
