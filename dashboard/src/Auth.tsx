import { useEffect, useState } from "react";
import { AudioLines, LockKeyhole, ShieldCheck, ArrowRight } from "lucide-react";
import { api, authenticate } from "./api";
import { Alert, Button, Field } from "./ui";
import type { Session } from "./types";
export default function Auth({
  onAuthenticated,
}: {
  onAuthenticated: (s: Session) => void;
}) {
  const [setup, setSetup] = useState<boolean | null>(null),
    [password, setPassword] = useState(""),
    [confirm, setConfirm] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [pairingToken,setPairingToken]=useState(()=>new URLSearchParams(window.location.hash.slice(1)).get('pair')||'');
  const [pairingRequired,setPairingRequired]=useState(false);
  useEffect(()=>{if(pairingToken&&window.location.hash)window.history.replaceState(null,'',window.location.pathname+window.location.search)},[pairingToken]);
  useEffect(() => {
    api("auth/status")
      .then((s) => {setSetup(s.setup_required ?? s.needs_setup ?? !s.configured);setPairingRequired(Boolean(s.pairing_required));})
      .catch((e) => setError(e.message));
  }, []);
  if (setup === null)
    return (
      <div className="boot">
        <AudioLines size={36} />
        <p>Connecting to your workspace…</p>
        {error && <Alert error>{error}</Alert>}
      </div>
    );
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (setup && password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      onAuthenticated(
        await authenticate(setup ? "auth/setup" : "auth/login", password,setup&&pairingToken?{pairing_token:pairingToken}:undefined),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-screen">
      <div className="auth-story">
        <div className="brand">
          <AudioLines />
          <div>
            PhoneDex<small>Control Centre</small>
          </div>
        </div>
        <div className="auth-headline">
          <span className="eyebrow">YOUR WORKSPACE. YOUR CONTROL.</span>
          <h1>
            A clearer view of
            <br />
            every conversation.
          </h1>
          <p>
            One local home for your AI phone agents,
            <br />
            call operations and personal information.
          </p>
        </div>
        <span className="local-note">
          <ShieldCheck size={17} /> Local by default. Yours by design.
        </span>
      </div>
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-lock">
          <LockKeyhole size={25} />
        </div>
        <h2>{setup ? "Make yourself at home." : "Welcome back."}</h2>
        <p>
          {setup
            ? "Create an operator password to secure your local workspace."
            : "Unlock your local PhoneDex workspace."}
        </p>
        {error && <Alert error>{error}</Alert>}
        {setup&&pairingRequired&&<Field label="Phone pairing code" hint="Filled automatically by your one-time setup link."><input required type="password" autoComplete="off" value={pairingToken} onChange={e=>setPairingToken(e.target.value)}/></Field>}
        <Field
          label="Operator password"
          hint={
            setup
              ? "Use at least 12 characters. This password protects sensitive actions."
              : undefined
          }
        >
          <input
            autoComplete={setup ? "new-password" : "current-password"}
            type="password"
            minLength={setup ? 12 : 1}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {setup && (
          <Field label="Confirm password">
            <input
              type="password"
              autoComplete="new-password"
              minLength={12}
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </Field>
        )}
        <Button type="submit" variant="primary" disabled={busy}>
          {busy
            ? "Unlocking…"
            : setup
              ? "Create workspace"
              : "Unlock workspace"}
          <ArrowRight size={17} />
        </Button>
        <small>
          <LockKeyhole size={13} /> Your session stays on this device.
        </small>
      </form>
    </div>
  );
}
