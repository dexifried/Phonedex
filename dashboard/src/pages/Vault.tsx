import { useEffect, useState } from "react";
import {
  LockKeyhole,
  Plus,
  ShieldCheck,
  Eye,
  EyeOff,
  Trash2,
  KeyRound,
  Clock,
  History,
  Pencil,
} from "lucide-react";
import { api } from "../api";
import type {
  Item,
  Workspace,
  VaultProfile,
  VaultField,
  VaultGrant,
  VaultAudit,
} from "../types";
import type { Guard } from "./Calls";
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
} from "../ui";
const categories = [
  "personal",
  "contact",
  "address",
  "government",
  "health",
  "insurance",
  "financial",
  "other",
];
const asList = (v: any, key: string): Item[] =>
  Array.isArray(v) ? v : v[key] || [];
export default function Vault({
  data,
  guard,
}: {
  data: Workspace;
  guard: Guard;
}) {
  const [profiles, setProfiles] = useState<VaultProfile[]>([]),
    [profile, setProfile] = useState(""),
    [fields, setFields] = useState<VaultField[]>([]),
    [grants, setGrants] = useState<VaultGrant[]>([]),
    [audit, setAudit] = useState<VaultAudit[]>([]),
    [tab, setTab] = useState("Profiles"),
    [error, setError] = useState(""),
    [modal, setModal] = useState<
      "profile" | "field" | "grant" | "delete-profile" | "delete-all" | null
    >(null),
    [edit, setEdit] = useState<Item | null>(null),
    [reveal, setReveal] = useState<Item>({});
  async function load() {
    try {
      const [p, g, a] = await Promise.all([
        api("vault/profiles"),
        api("vault/grants"),
        api("vault/audit"),
      ]);
      const list = asList(p, "profiles") as VaultProfile[];
      setProfiles(list);
      setGrants(asList(g, "grants") as VaultGrant[]);
      setAudit(asList(a, "audit") as VaultAudit[]);
      setProfile((old) =>
        list.some((p) => p.id === old) ? old : list[0]?.id || "",
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    setReveal({});
    if (profile)
      api(`vault/profiles/${profile}/fields`)
        .then((v) => setFields(asList(v, "fields") as VaultField[]))
        .catch((e) => setError(e.message));
    else setFields([]);
  }, [profile]);
  async function reloadFields() {
    await load();
    if (profile) {
      const f = await api(`vault/profiles/${profile}/fields`);
      setFields(asList(f, "fields") as VaultField[]);
    }
  }
  async function remove(path: string) {
    try {
      await api(path, "DELETE");
      setModal(null);
      setReveal({});
      await reloadFields();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <PageHeading
        title="Personal. Protected. Permissioned."
        description="Your information stays encrypted until you choose exactly how it can be used."
        action={
          <Button variant="primary" onClick={() => setModal("profile")}>
            <Plus size={16} />
            New profile
          </Button>
        }
      />
      <div className="vault-assurance">
        <div className="stat-icon cyan">
          <ShieldCheck size={22} />
        </div>
        <div>
          <strong>Encrypted at rest. Shared by explicit permission.</strong>
          <p>
            No personal information is added to agent prompts. Every disclosure
            needs a scoped, expiring grant.
          </p>
        </div>
        <Badge tone="cyan">Local vault</Badge>
      </div>
      {error && <Alert error>{error}</Alert>}
      <div className="tabs section-gap">
        {["Profiles", "Disclosure grants", "Access audit"].map((t) => (
          <button
            key={t}
            className={tab === t ? "selected" : ""}
            onClick={() => {
              setTab(t);
              load();
            }}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "Profiles" ? (
        <div className="vault-layout">
          <Panel
            title="Your profiles"
            action={
              <button
                className="icon-button"
                aria-label="Add profile"
                onClick={() => setModal("profile")}
              >
                <Plus size={16} />
              </button>
            }
          >
            <div className="profile-list">
              {profiles.map((p) => (
                <button
                  key={p.id}
                  className={p.id === profile ? "selected" : ""}
                  onClick={() => setProfile(p.id)}
                >
                  <div className="avatar">
                    <LockKeyhole size={17} />
                  </div>
                  <span>
                    <strong>{p.name}</strong>
                    <small>{p.field_count ?? "—"} saved fields</small>
                  </span>
                </button>
              ))}
              {!profiles.length && (
                <Empty
                  title="No profiles yet"
                  description="Create a profile to store encrypted personal fields."
                />
              )}
            </div>
          </Panel>
          <Panel
            title={
              profiles.find((p) => p.id === profile)?.name ||
              "Personal information"
            }
            icon={<LockKeyhole size={17} />}
            action={
              profile && (
                <Button
                  onClick={() => {
                    setEdit(null);
                    setModal("field");
                  }}
                >
                  <Plus size={15} />
                  Add field
                </Button>
              )
            }
          >
            {fields.length ? (
              <div className="vault-fields">
                {fields.map((f) => (
                  <div className="vault-field" key={f.id}>
                    <div>
                      <strong>{f.label}</strong>
                      <small>
                        {f.key} · {f.category || f.sensitivity || "sensitive"}
                      </small>
                    </div>
                    <span
                      className={`secret-value ${reveal[f.id] ? "revealed" : ""}`}
                    >
                      {reveal[f.id] || "••••••••••••"}
                    </span>
                    <button
                      className="icon-button"
                      aria-label={
                        reveal[f.id] ? `Hide ${f.label}` : `Reveal ${f.label}`
                      }
                      onClick={() =>
                        reveal[f.id]
                          ? setReveal({ ...reveal, [f.id]: undefined })
                          : guard(async () => {
                              const r = await api(
                                `vault/fields/${f.id}/reveal`,
                                "POST",
                                {},
                              );
                              setReveal((old) => ({ ...old, [f.id]: r.value }));
                              setTimeout(
                                () =>
                                  setReveal((old) => ({
                                    ...old,
                                    [f.id]: undefined,
                                  })),
                                20000,
                              );
                              await load();
                            }, `Reveal ${f.label}`)
                      }
                    >
                      {reveal[f.id] ? <EyeOff size={17} /> : <Eye size={17} />}
                    </button>
                    <button
                      className="icon-button"
                      aria-label={`Edit ${f.label}`}
                      onClick={() => {
                        setEdit(f);
                        setModal("field");
                      }}
                    >
                      <Pencil size={16} />
                    </button>
                    <button
                      className="icon-button danger-text"
                      aria-label={`Delete ${f.label}`}
                      onClick={() => {
                        setEdit(f);
                        setModal("delete-profile");
                      }}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                icon={<KeyRound size={30} />}
                title="Your private information belongs here."
                description={
                  profile
                    ? "Add encrypted fields such as addresses, membership details or contact information."
                    : "Choose or create a profile to get started."
                }
                action={
                  profile && (
                    <Button
                      onClick={() => {
                        setEdit(null);
                        setModal("field");
                      }}
                    >
                      <Plus size={15} />
                      Add your first field
                    </Button>
                  )
                }
              />
            )}
            <div className="panel-foot">
              <span>
                <ShieldCheck size={14} /> Values are always masked by default.
              </span>
              {profile && (
                <button
                  className="text-button danger-text"
                  onClick={() => {
                    setEdit(null);
                    setModal("delete-profile");
                  }}
                >
                  Delete profile
                </button>
              )}
            </div>
          </Panel>
        </div>
      ) : tab === "Disclosure grants" ? (
        <Panel
          title="Disclosure grants"
          icon={<KeyRound size={18} />}
          action={
            <Button onClick={() => setModal("grant")} disabled={!fields.length}>
              <Plus size={15} />
              Create grant
            </Button>
          }
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Exact recipient</th>
                  <th>Purpose</th>
                  <th>Expires</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {grants.map((g) => (
                  <tr key={g.id}>
                    <td>
                      {fields.find((f) => f.id === g.field_id)?.label ||
                        g.field_id}
                      <small>Call {g.call_id}</small>
                    </td>
                    <td>{g.recipient}</td>
                    <td>{g.purpose}</td>
                    <td>{date(new Date(g.expires_at * 1000).toISOString())}</td>
                    <td>
                      <Badge tone={g.active ? "green" : ""}>
                        {g.active
                          ? "Active"
                          : g.revoked_at
                            ? "Revoked"
                            : "Expired"}
                      </Badge>
                    </td>
                    <td>
                      {g.active && data.calls.some(c=>c.id===g.call_id && Array.isArray(c.controls) && c.controls.includes('profile_field')) && (
                        <Button onClick={()=>guard(async()=>{
                          try {
                            await api(`calls/${g.call_id}/control`,'POST',{kind:'profile_field',payload:{field_id:g.field_id,purpose:g.purpose},confirm:true});
                            setError('');
                            await load();
                          } catch(e){setError((e as Error).message)}
                        },'Supply only this authorized field to the active call agent')}>
                          Supply to call
                        </Button>
                      )}
                      <Button
                        disabled={!g.active}
                        onClick={() => remove(`vault/grants/${g.id}`)}
                      >
                        Revoke
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!grants.length && (
              <Empty
                icon={<ShieldCheck size={30} />}
                title="No information is shared."
                description="Create an explicit grant for one field, one call, one recipient and one purpose."
              />
            )}
          </div>
        </Panel>
      ) : (
        <Panel title="Access audit" icon={<History size={18} />}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Action</th>
                  <th>Decision</th>
                  <th>Field reference</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((a) => (
                  <tr key={a.id}>
                    <td>{date(new Date(a.timestamp * 1000).toISOString())}</td>
                    <td>{a.event}</td>
                    <td>
                      <Badge tone={a.decision === "allow" ? "green" : "red"}>
                        {a.decision}
                      </Badge>
                    </td>
                    <td className="mono">{a.field_id || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!audit.length && (
              <Empty
                title="No access events"
                description="The audit records actions and decisions without storing personal values."
              />
            )}
          </div>
        </Panel>
      )}
      <div className="privacy-footer">
        <span>
          <LockKeyhole size={14} /> Revealed values hide again after 20 seconds.
        </span>
        <button
          className="text-button danger-text"
          onClick={() => setModal("delete-all")}
        >
          Delete all vault data
        </button>
      </div>
      {modal === "profile" && (
        <ProfileForm
          onClose={() => setModal(null)}
          onSaved={async () => {
            setModal(null);
            await load();
          }}
        />
      )}
      {modal === "field" && (
        <FieldForm
          profile={profile}
          edit={edit}
          onClose={() => setModal(null)}
          onSaved={async () => {
            setModal(null);
            await reloadFields();
          }}
        />
      )}
      {modal === "grant" && (
        <GrantForm
          fields={fields}
          data={data}
          guard={guard}
          onClose={() => setModal(null)}
          onSaved={async () => {
            setModal(null);
            await load();
          }}
        />
      )}
      {(modal === "delete-profile" || modal === "delete-all") && (
        <Modal
          title={
            modal === "delete-all"
              ? "Delete all vault data"
              : edit
                ? `Delete ${edit.label}`
                : "Delete profile"
          }
          onClose={() => setModal(null)}
        >
          <Alert error>
            This permanently deletes{" "}
            {modal === "delete-all"
              ? "all encrypted personal fields, profiles and grants"
              : edit
                ? "this field and its grants"
                : "this profile, its encrypted fields and grants"}{" "}
            from the local vault.
          </Alert>
          <div className="modal-actions">
            <Button onClick={() => setModal(null)}>Cancel</Button>
            <Button
              variant="danger"
              onClick={() =>
                guard(
                  () =>
                    remove(
                      modal === "delete-all"
                        ? "vault"
                        : edit
                          ? `vault/fields/${edit.id}`
                          : `vault/profiles/${profile}`,
                    ),
                  "Delete encrypted personal information",
                )
              }
            >
              Confirm deletion
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
function ProfileForm({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(""),
    [error, setError] = useState("");
  return (
    <Modal title="New personal profile" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api("vault/profiles", "POST", { name });
            await onSaved();
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        {error && <Alert error>{error}</Alert>}
        <Field label="Profile name">
          <input
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Personal, family, business…"
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary">Create profile</Button>
        </div>
      </form>
    </Modal>
  );
}
function FieldForm({
  profile,
  edit,
  onClose,
  onSaved,
}: {
  profile: string;
  edit: Item | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState({
      key: edit?.key || "",
      label: edit?.label || "",
      value: "",
      sensitivity: edit?.category || edit?.sensitivity || "personal",
    }),
    [error, setError] = useState("");
  return (
    <Modal
      title={edit ? "Update encrypted field" : "Add encrypted field"}
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api(`vault/profiles/${profile}/fields`, "POST", form);
            setForm({ ...form, value: "" });
            await onSaved();
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        {error && <Alert error>{error}</Alert>}
        <div className="form-grid">
          <Field label="Field label">
            <input
              required
              value={form.label}
              onChange={(e) =>
                setForm({
                  ...form,
                  label: e.target.value,
                  key: edit
                    ? form.key
                    : e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_"),
                })
              }
            />
          </Field>
          <Field label="Category">
            <select
              value={form.sensitivity}
              onChange={(e) =>
                setForm({ ...form, sensitivity: e.target.value })
              }
            >
              {categories.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </Field>
        </div>
        <Field
          label="Stable field key"
          hint="An exact identifier. The agent cannot guess missing keys."
        >
          <input
            required
            disabled={Boolean(edit)}
            pattern="[a-zA-Z0-9_.-]+"
            value={form.key}
            onChange={(e) => setForm({ ...form, key: e.target.value })}
          />
        </Field>
        <Field
          label={edit ? "Replacement value" : "Private value"}
          hint="Encrypted before storage. Sensitive categories are always protected."
        >
          <input
            required
            type="password"
            autoComplete="new-password"
            value={form.value}
            onChange={(e) => setForm({ ...form, value: e.target.value })}
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary">Save encrypted field</Button>
        </div>
      </form>
    </Modal>
  );
}
function GrantForm({
  fields,
  data,
  guard,
  onClose,
  onSaved,
}: {
  fields: Item[];
  data: Workspace;
  guard: Guard;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState({
      field_id: fields[0]?.id || "",
      call_id: "",
      recipient: "",
      purpose: "",
      expires_seconds: 300,
    }),
    [error, setError] = useState("");
  return (
    <Modal title="Authorize one disclosure" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          guard(async () => {
            try {
              await api("vault/grants", "POST", form);
              await onSaved();
            } catch (e) {
              setError((e as Error).message);
            }
          }, "Authorize this exact disclosure");
        }}
      >
        <Alert>
          Spoken disclosure cannot be recalled. Permission is limited to this
          exact field, call, recipient and purpose.
        </Alert>
        {error && <Alert error>{error}</Alert>}
        <Field label="Personal field">
          <select
            required
            value={form.field_id}
            onChange={(e) => setForm({ ...form, field_id: e.target.value })}
          >
            {fields.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Exact call">
          <select
            required
            value={form.call_id}
            onChange={(e) => setForm({ ...form, call_id: e.target.value })}
          >
            <option value="">Select an existing call</option>
            {data.calls.map((c) => (
              <option key={c.id} value={c.id}>
                {c.contact || c.number_masked} · {c.id}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Exact recipient"
          hint="Enter the exact E.164 recipient number asserted by the runtime."
        >
          <input
            required
            type="tel"
            value={form.recipient}
            onChange={(e) => setForm({ ...form, recipient: e.target.value })}
          />
        </Field>
        <Field label="Exact permitted purpose">
          <input
            required
            value={form.purpose}
            onChange={(e) => setForm({ ...form, purpose: e.target.value })}
          />
        </Field>
        <Field label="Expires in seconds">
          <input
            required
            type="number"
            min={1}
            max={300}
            value={form.expires_seconds}
            onChange={(e) =>
              setForm({ ...form, expires_seconds: Number(e.target.value) })
            }
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary">
            <ShieldCheck size={16} />
            Review & authorize
          </Button>
        </div>
      </form>
    </Modal>
  );
}
