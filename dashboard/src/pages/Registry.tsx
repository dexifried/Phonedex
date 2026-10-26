import { useState } from "react";
import {
  Bot,
  Users,
  Network,
  Plus,
  MoreHorizontal,
  Pencil,
  Trash2,
  ShieldCheck,
  FileText,
  CalendarClock,
  Search,
  CheckCircle2,
} from "lucide-react";
import type { Item, Workspace, Agent, Contact, Provider, Task } from "../types";
import { api } from "../api";
import {
  Alert,
  Badge,
  Button,
  Empty,
  Field,
  Modal,
  PageHeading,
  Panel,
} from "../ui";
export type RegistryKind = "agents" | "contacts" | "providers" | "tasks";
type RegistryItem = Partial<Agent & Contact & Provider & Task> &
  Pick<Agent, "id" | "name">;
const configs = {
  agents: {
    title: "Agents, with purpose.",
    description: "Shape the voice and instructions behind each conversation.",
    singular: "agent",
    icon: Bot,
  },
  contacts: {
    title: "People. Not just numbers.",
    description: "Your local address book, with explicit outbound permissions.",
    singular: "contact",
    icon: Users,
  },
  providers: {
    title: "Your connections, configured.",
    description:
      "Encrypted credentials and local SIP configuration. Activation stays in your hands.",
    singular: "provider",
    icon: Network,
  },
  tasks: {
    title: "Prepare once. Act deliberately.",
    description: "Keep reusable call tasks ready for your next conversation.",
    singular: "task",
    icon: CalendarClock,
  },
};
const templates = [
  {
    name: "Appointment assistant",
    description: "Schedule and confirm appointments.",
    prompt:
      "Help the recipient arrange an appointment. Confirm the date, time and next steps. Ask for clarification when needed.",
  },
  {
    name: "Customer enquiry",
    description: "Collect clear answers to a specific question.",
    prompt:
      "Introduce yourself as an AI assistant. Ask the requested questions respectfully, record the answers and confirm any follow-up.",
  },
  {
    name: "General assistant",
    description: "A concise, helpful phone assistant.",
    prompt:
      "Introduce yourself clearly as an AI assistant. Complete the operator’s task without inventing information. Confirm important details.",
  },
];
export default function Registry({
  kind,
  data,
  refresh,
  onPrepare,
}: {
  kind: RegistryKind;
  data: Workspace;
  refresh: () => Promise<void>;
  onPrepare: () => void;
}) {
  const cfg = configs[kind],
    Icon = cfg.icon;
  const [editing, setEditing] = useState<Item | null>(null),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [deleting, setDeleting] = useState<Item | null>(null),
    [validation, setValidation] = useState<Item | null>(null);
  const list: RegistryItem[] = data[kind].filter((x: RegistryItem) =>
    [x.name, x.description, x.number, x.company]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  async function remove() {
    try {
      await api(`${kind}/${deleting!.id}`, "DELETE");
      setDeleting(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function validate(item: Item) {
    try {
      const result = await api(`providers/${item.id}/validate`, "POST", {});
      setValidation(result);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <PageHeading
        title={cfg.title}
        description={cfg.description}
        action={
          <Button variant="primary" onClick={() => setEditing({})}>
            <Plus size={17} />
            New {cfg.singular}
          </Button>
        }
      />
      {kind === "tasks" && (
        <Alert>
          Automations prepare local tasks only. Scheduled dialing is
          unavailable; each outbound call needs an explicit review and password
          confirmation.
        </Alert>
      )}
      {kind === "providers" && (
        <Alert>
          Validation checks local configuration only. It does not test carrier
          connectivity or activate a gateway.
        </Alert>
      )}
      {error && <Alert error>{error}</Alert>}
      <div className="filter-bar">
        <div className="search-input">
          <Search size={16} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={`Search ${kind}`}
            placeholder={`Search ${kind}…`}
          />
        </div>
        <span className="subtle">
          {list.length} {kind}
        </span>
      </div>
      {list.length ? (
        <div className="card-grid">
          {list.map((item) => (
            <Panel key={item.id} className="registry-card">
              <div className="row">
                <div className="stat-icon violet">
                  <Icon size={22} />
                </div>
                <Badge
                  tone={
                    kind === "contacts"
                      ? item.allowed
                        ? "green"
                        : ""
                      : item.enabled
                        ? "green"
                        : ""
                  }
                >
                  {kind === "contacts"
                    ? item.allowed
                      ? "Allowlisted"
                      : "Restricted"
                    : kind === "tasks"
                      ? item.status || "draft"
                      : item.enabled
                        ? "Enabled"
                        : "Inactive"}
                </Badge>
              </div>
              <h2>{item.name}</h2>
              <p>
                {item.description ||
                  item.company ||
                  item.kind ||
                  "No description added"}
              </p>
              {kind === "contacts" && (
                <>
                  <div className="contact-number mono">{item.number}</div>
                  <div className="tag-list">
                    {(item.tags || []).map((t: string) => (
                      <Badge key={t}>{t}</Badge>
                    ))}
                  </div>
                </>
              )}
              {kind === "agents" && (
                <div className="card-meta">
                  <span>
                    {item.voice || "marin"} <small>Core voice setting</small>
                  </span>
                  <span>{item.model || "gpt-realtime"}</span>
                </div>
              )}
              {kind === "providers" && (
                <div className="card-meta">
                  <ShieldCheck size={15} />
                  <span>
                    {
                      Object.values(item.credential_presence || {}).filter(
                        Boolean,
                      ).length
                    }{" "}
                    encrypted credentials saved
                  </span>
                </div>
              )}
              {kind === "tasks" && (
                <p>{item.prompt || "No task instructions"}</p>
              )}
              <div className="card-actions">
                <Button onClick={() => setEditing(item)}>
                  <Pencil size={14} />
                  Edit
                </Button>
                {kind === "providers" && (
                  <Button onClick={() => validate(item)}>
                    <ShieldCheck size={14} />
                    Validate locally
                  </Button>
                )}
                {kind === "tasks" && (
                  <Button onClick={onPrepare}>Prepare call</Button>
                )}
                <button
                  className="icon-button danger-text"
                  aria-label={`Delete ${item.name}`}
                  onClick={() => setDeleting(item)}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </Panel>
          ))}
        </div>
      ) : (
        <Panel>
          <Empty
            icon={<Icon size={32} />}
            title={`Your first ${cfg.singular} starts here.`}
            description={`Add a ${cfg.singular} to make this workspace yours.`}
            action={
              <Button onClick={() => setEditing({})}>
                <Plus size={15} />
                New {cfg.singular}
              </Button>
            }
          />
        </Panel>
      )}
      {kind === "tasks" && (
        <Panel
          title="Schedules"
          icon={<CalendarClock size={18} />}
          className="section-gap"
        >
          <Empty
            title="Scheduled dialing is unavailable"
            description="Tasks stay local drafts until you explicitly prepare and confirm a call."
            action={<Button disabled>Create schedule · unavailable</Button>}
          />
        </Panel>
      )}
      {editing && (
        <EditForm
          kind={kind}
          item={editing}
          data={data}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}
      {deleting && (
        <Modal
          title={`Delete ${cfg.singular}`}
          onClose={() => setDeleting(null)}
        >
          <p>Delete “{deleting.name}” from this local workspace?</p>
          <div className="modal-actions">
            <Button onClick={() => setDeleting(null)}>Cancel</Button>
            <Button variant="danger" onClick={remove}>
              Delete {cfg.singular}
            </Button>
          </div>
        </Modal>
      )}
      {validation && (
        <Modal
          title="Local configuration validation"
          onClose={() => setValidation(null)}
        >
          <Alert>
            Local validation completed. Carrier reachability has not been
            tested.
          </Alert>
          <div className="results">
            {Object.entries(validation).map(([k, v]) => (
              <div className="result-pair" key={k}>
                <span>{k.replaceAll("_", " ")}</span>
                <strong>
                  {typeof v === "object"
                    ? Array.isArray(v)
                      ? v.join(", ")
                      : Object.entries(v || {})
                          .map(([a, b]) => `${a}: ${b}`)
                          .join(" · ")
                    : String(v)}
                </strong>
              </div>
            ))}
          </div>
          <div className="modal-actions">
            <Button onClick={() => setValidation(null)}>Done</Button>
          </div>
        </Modal>
      )}
    </>
  );
}
function EditForm({
  kind,
  item,
  data,
  onClose,
  onSaved,
}: {
  kind: RegistryKind;
  item: Item;
  data: Workspace;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const base =
    kind === "agents"
      ? {
          name: "",
          description: "",
          prompt: "",
          voice: "marin",
          model: "gpt-realtime",
          enabled: true,
          extension: "",
          tools: [],
          mode: "normal",
          max_duration_seconds: 120,
        }
      : kind === "contacts"
        ? {
            name: "",
            number: "",
            company: "",
            notes: "",
            allowed: false,
            tags: [],
          }
        : kind === "providers"
          ? { name: "", kind: "sip", config: {}, enabled: false }
          : {
              name: "",
              description: "",
              contact_id: "",
              agent_id: "",
              prompt: "",
              status: "draft",
            };
  const [form, setForm] = useState<Item>({ ...base, ...item }),
    [credentials, setCredentials] = useState<Item>({}),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const change = (key: string, value: any) =>
    setForm({ ...form, [key]: value });
  const input = (key: string, label: string, type = "text", hint?: string) => (
    <Field label={label} hint={hint}>
      <input
        required={key === "name" || key === "number"}
        type={type}
        disabled={kind === "agents" && ["voice", "model"].includes(key)}
        value={form[key] || ""}
        onChange={(e) => change(key, e.target.value)}
      />
    </Field>
  );
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const keys =
        kind === "agents"
          ? [
              "name",
              "description",
              "prompt",
              "voice",
              "model",
              "enabled",
              "extension",
              "tools",
              "mode",
              "max_duration_seconds",
            ]
          : kind === "contacts"
            ? ["name", "number", "company", "notes", "allowed", "tags"]
            : kind === "providers"
              ? ["name", "kind", "config", "enabled"]
              : [
                  "name",
                  "description",
                  "contact_id",
                  "agent_id",
                  "status",
                  "prompt",
                ];
      const body: Item = Object.fromEntries(
        keys.filter((k) => form[k] !== undefined).map((k) => [k, form[k]]),
      );
      if (kind === "providers") {
        const nonempty = Object.fromEntries(
          Object.entries(credentials).filter(([, v]) => v),
        );
        if (Object.keys(nonempty).length) body.credentials = nonempty;
      }
      await api(
        item.id ? `${kind}/${item.id}` : kind,
        item.id ? "PATCH" : "POST",
        body,
      );
      await onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`${item.id ? "Edit" : "New"} ${configs[kind].singular}`}
      onClose={onClose}
      wide
    >
      <form onSubmit={save}>
        {error && <Alert error>{error}</Alert>}
        {kind === "agents" && !item.id && (
          <Field label="Start from a template">
            <select
              defaultValue=""
              onChange={(e) => {
                const t = templates[Number(e.target.value)];
                if (t) setForm({ ...form, ...t });
              }}
            >
              <option value="">Choose a starting point</option>
              {templates.map((t, i) => (
                <option value={i} key={t.name}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        {input("name", "Name")}
        {kind !== "contacts" &&
          kind !== "providers" &&
          input("description", "Purpose")}
        {kind === "agents" && (
          <>
            <Field
              label="System prompt"
              hint="Do not place private information or credentials in agent prompts."
            >
              <textarea
                rows={5}
                value={form.prompt}
                onChange={(e) => change("prompt", e.target.value)}
              />
            </Field>
            <div className="form-grid">
              {input(
                "voice",
                "Voice setting",
                "text",
                "Core default. Voice selection is unavailable in the current runtime.",
              )}
              {input(
                "model",
                "Model setting",
                "text",
                "Core default. Model selection is unavailable in the current runtime.",
              )}
              {input(
                "extension",
                "Agent extension",
                "text",
                "Optional numeric extension",
              )}
              <Field label="Call mode">
                <select
                  value={form.mode}
                  onChange={(e) => change("mode", e.target.value)}
                >
                  <option>normal</option>
                  <option>business</option>
                </select>
              </Field>
              <Field label="Maximum duration (seconds)">
                <input
                  type="number"
                  min={20}
                  max={300}
                  required
                  value={form.max_duration_seconds || 120}
                  onChange={(e) =>
                    change("max_duration_seconds", Number(e.target.value))
                  }
                />
              </Field>
            </div>
            <Alert>
              Supported runtime policies: one concurrent outbound call; maximum
              300 seconds. Human transfer, hold, mute and conference are
              unavailable.
            </Alert>
            <label className="check">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => change("enabled", e.target.checked)}
              />
              Enabled
            </label>
          </>
        )}
        {kind === "contacts" && (
          <>
            <div className="form-grid">
              {input(
                "number",
                "Phone number",
                "tel",
                "Use E.164 format, for example +14165550100.",
              )}
              {input("company", "Company")}
            </div>
            <Field label="Tags">
              <input
                value={(form.tags || []).join(", ")}
                placeholder="Personal, appointments"
                onChange={(e) =>
                  change(
                    "tags",
                    e.target.value
                      .split(",")
                      .map((t) => t.trim())
                      .filter(Boolean),
                  )
                }
              />
            </Field>
            <Field label="Notes">
              <textarea
                rows={3}
                value={form.notes || ""}
                onChange={(e) => change("notes", e.target.value)}
              />
            </Field>
            <label className="check">
              <input
                type="checkbox"
                checked={form.allowed}
                onChange={(e) => change("allowed", e.target.checked)}
              />
              Allow outbound calls to this exact number
            </label>
            <p className="subtle">
              Allowlisting never places a call automatically.
            </p>
          </>
        )}
        {kind === "providers" && (
          <>
            <Field label="Provider type">
              <select
                value={form.kind}
                onChange={(e) => change("kind", e.target.value)}
              >
                {[
                  "sip",
                  "telnyx",
                  "twilio",
                  "signalwire",
                  "generic",
                  "worker",
                  "openai",
                ].map((k) => (
                  <option key={k}>{k}</option>
                ))}
              </select>
            </Field>
            <div className="form-grid">
              {[
                "termination_uri",
                "caller_id",
                "public_ip",
                "gateway_url",
                "region",
                "transport",
              ].map((k) => (
                <Field key={k} label={k.replaceAll("_", " ")}>
                  <input
                    value={form.config?.[k] || ""}
                    onChange={(e) =>
                      change("config", { ...form.config, [k]: e.target.value })
                    }
                  />
                </Field>
              ))}
            </div>
            <Field label="Allowed recipient numbers">
              <input
                placeholder="+14165550100, +14165550101"
                value={
                  Array.isArray(form.config?.allowed_recipients)
                    ? form.config.allowed_recipients.join(", ")
                    : form.config?.allowed_recipients || ""
                }
                onChange={(e) =>
                  change("config", {
                    ...form.config,
                    allowed_recipients: e.target.value,
                  })
                }
              />
            </Field>
            <h3>Encrypted credentials</h3>
            <p className="subtle">
              Existing values are never returned. Leave a field blank to keep
              its saved value.
            </p>
            <div className="form-grid">
              {[
                "username",
                "password",
                "api_key",
                "token",
                "account_sid",
                "auth_token",
              ].map((k) => (
                <Field
                  key={k}
                  label={k.replaceAll("_", " ")}
                  hint={
                    item.credential_presence?.[k] ? "Saved securely" : undefined
                  }
                >
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={credentials[k] || ""}
                    onChange={(e) =>
                      setCredentials({ ...credentials, [k]: e.target.value })
                    }
                  />
                </Field>
              ))}
            </div>
            {form.kind === "worker" && <label className="check">
              <input type="checkbox" checked={form.config?.cache_transcripts === true}
                onChange={e => change("config", { ...form.config, cache_transcripts: e.target.checked })} />
              Store copies of remote transcripts on this computer
            </label>}
            <label className="check">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => change("enabled", e.target.checked)}
              />
              Mark configuration enabled
            </label>
            <Alert>{form.kind === "worker" ? "Enabled control providers synchronize through the existing API. Saving never starts or restarts the telephony gateway." : "Saving does not start, restart or connect a gateway."}</Alert>
          </>
        )}
        {kind === "tasks" && (
          <>
            <div className="form-grid">
              <Field label="Contact">
                <select
                  value={form.contact_id}
                  onChange={(e) => change("contact_id", e.target.value)}
                >
                  <option value="">Choose later</option>
                  {data.contacts.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Agent">
                <select
                  value={form.agent_id}
                  onChange={(e) => change("agent_id", e.target.value)}
                >
                  <option value="">Choose later</option>
                  {data.agents.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="Task instructions">
              <textarea
                rows={4}
                value={form.prompt || ""}
                onChange={(e) => change("prompt", e.target.value)}
              />
            </Field>
            <Field label="Status">
              <select
                value={form.status}
                onChange={(e) => change("status", e.target.value)}
              >
                {["draft", "completed", "cancelled"].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </Field>
          </>
        )}
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy}>
            {busy ? "Saving…" : `Save ${configs[kind].singular}`}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
