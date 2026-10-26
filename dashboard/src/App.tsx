import { useCallback, useEffect, useRef, useState } from "react";
import {
  Home,
  Phone,
  Clock,
  FileText,
  Users,
  LockKeyhole,
  ContactRound,
  Link,
  MessageCircle,
  ChartNoAxesColumn,
  Terminal,
  Workflow,
  Settings as SettingsIcon,
  Search,
  Bell,
  ChevronDown,
  AudioLines,
  Menu,
  X,
  PanelLeftClose,
  LogOut,
  Command,
  ShieldCheck,
  ArrowRight,
} from "lucide-react";
import type { Call, Page, Session, Workspace } from "./types";
import { api, authenticate, setSession, ApiError } from "./api";
import { Alert, Button, Empty, Field, Loading, Modal } from "./ui";
import Auth from "./Auth";
import Overview from "./pages/Overview";
import { CallDetail, CallsPage, PrepareCall, type Guard } from "./pages/Calls";
import Registry from "./pages/Registry";
import Vault from "./pages/Vault";
import { Analytics, Logs, Messages, Settings } from "./pages/Operations";
const navigation: { page: Page; icon: typeof Home; label?: string }[] = [
  { page: "Overview", icon: Home },
  { page: "Live Calls", icon: Phone },
  { page: "Call History", icon: Clock },
  { page: "Transcripts", icon: FileText },
  { page: "Agents", icon: Users },
  { page: "Personal Vault", icon: LockKeyhole },
  { page: "Contacts", icon: ContactRound },
  { page: "Providers & SIP", icon: Link },
  { page: "Messages", icon: MessageCircle, label: "Messages (Unavailable)" },
  { page: "Analytics", icon: ChartNoAxesColumn },
  { page: "Live Logs", icon: Terminal },
  { page: "Automations", icon: Workflow },
  { page: "Settings", icon: SettingsIcon },
];
const emptyWorkspace: Workspace = {
  health: {},
  stats: {},
  calls: [],
  agents: [],
  contacts: [],
  providers: [],
  tasks: [],
  logs: [],
  activity: [],
  capabilities: {},
};
export default function App() {
  const [session, updateSession] = useState<Session | null>(null),
    [checking, setChecking] = useState(true),
    [data, setData] = useState<Workspace | null>(null),
    [page, setPage] = useState<Page>("Overview"),
    [selected, setSelected] = useState<Call | null>(null),
    [mobile, setMobile] = useState(false),
    [collapsed, setCollapsed] = useState(false),
    [prepare, setPrepare] = useState(false),
    [error, setError] = useState(""),
    [offline, setOffline] = useState(false),
    [dismissed, setDismissed] = useState(false),
    [search, setSearch] = useState(""),
    [searchOpen, setSearchOpen] = useState(false),
    [notifications, setNotifications] = useState(false),
    [account, setAccount] = useState(false),
    [guarded, setGuarded] = useState<{
      action: () => Promise<void>;
      reason: string;
    } | null>(null),
    [theme, setThemeState] = useState(
      () => localStorage.getItem("phonedex.theme.v1") || "dark",
    );
  const searchRef = useRef<HTMLInputElement>(null);
  const setTheme = (v: string) => {
    setThemeState(v);
    localStorage.setItem("phonedex.theme.v1", v);
  };
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    api<Session>("auth/session")
      .then((s) => {
        setSession(s);
        updateSession(s);
      })
      .catch(() => updateSession({ authenticated: false }))
      .finally(() => setChecking(false));
  }, []);
  const refresh = useCallback(async () => {
    try {
      const value = await api<Workspace>("workspace");
      setData({ ...emptyWorkspace, ...value });
      setOffline(false);
      setError("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        updateSession({ authenticated: false });
        setData(null);
        return;
      }
      setOffline(true);
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    if (!session?.authenticated) return;
    refresh();
    let timer: number | undefined;
    const events = new EventSource("/api/events", { withCredentials: true });
    const change = () => {
      clearTimeout(timer);
      timer = window.setTimeout(refresh, 180);
    };
    events.addEventListener("change", change);
    events.addEventListener("snapshot", change);
    events.onopen = () => {
      setOffline(false);
      refresh();
    };
    events.onerror = () => setOffline(true);
    const interval = window.setInterval(refresh, 30000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
      events.close();
    };
  }, [session?.authenticated, refresh]);
  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        searchRef.current?.focus();
        setSearchOpen(true);
      }
      if (e.key === "Escape") {
        setSearchOpen(false);
        setMobile(false);
        setNotifications(false);
        setAccount(false);
      }
    };
    document.addEventListener("keydown", fn);
    return () => document.removeEventListener("keydown", fn);
  }, []);
  const navigate = (next: Page) => {
    setPage(next);
    setSelected(null);
    setMobile(false);
    setSearchOpen(false);
    setSearch("");
  };
  const onCall = (c: Call) => {
    setSelected(c);
    setMobile(false);
    setSearchOpen(false);
  };
  const guard: Guard = (action, reason) => setGuarded({ action, reason });
  const displayed =
    (selected && data?.calls.find((c) => c.id === selected.id)) || selected;
  async function logout() {
    try {
      await api("auth/logout", "POST", {});
      setData(null);
      updateSession({ authenticated: false });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (checking)
    return (
      <div className="boot">
        <AudioLines size={40} />
        <Loading />
      </div>
    );
  if (!session?.authenticated)
    return (
      <Auth
        onAuthenticated={(s) => {
          setSession(s);
          updateSession(s);
        }}
      />
    );
  const workspace = data || emptyWorkspace;
  const found = search.trim()
    ? workspace.calls
        .filter((c) =>
          [c.contact, c.agent_name, c.number_masked]
            .join(" ")
            .toLowerCase()
            .includes(search.toLowerCase()),
        )
        .slice(0, 5)
    : [];
  return (
    <div className={`app ${collapsed ? "collapsed" : ""}`}>
      <div
        className={`sidebar-backdrop ${mobile ? "visible" : ""}`}
        onClick={() => setMobile(false)}
      />
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <button
          className="brand"
          onClick={() => navigate("Overview")}
          aria-label="PhoneDex overview"
        >
          <AudioLines />
          <div>
            PhoneDex<small>Control Centre</small>
          </div>
        </button>
        <button
          className="mobile-close icon-button"
          onClick={() => setMobile(false)}
          aria-label="Close navigation"
        >
          <X size={20} />
        </button>
        <div className="nav-caption">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map(({ page: p, icon: Icon, label }) => (
            <button
              aria-label={label||p}
              key={p}
              onClick={() => navigate(p)}
              className={`${page === p ? "active" : ""} ${p === "Messages" ? "unavailable" : ""}`}
              title={collapsed ? label || p : undefined}
            >
              <Icon size={18} />
              <span>{label || p}</span>
              {p === "Live Calls" &&
                (workspace.stats.active_calls ?? 0) > 0 && (
                  <b>{workspace.stats.active_calls}</b>
                )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-workspace">
            <div className="workspace-symbol">
              <ShieldCheck size={19} />
            </div>
            <div>
              <strong>Local workspace</strong>
              <small>Private by design</small>
            </div>
            <span className="dot green" />
          </div>
          <button
            className="collapse-button"
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <PanelLeftClose size={16} />
            <span>Collapse sidebar</span>
          </button>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label="Open navigation"
            onClick={() => setMobile(true)}
          >
            <Menu size={21} />
          </button>
          <div className="breadcrumbs">
            <span>Workspace</span>
            <i>/</i>
            <strong>{displayed ? "Call detail" : page}</strong>
          </div>
          <div className="top-search">
            <Search size={16} />
            <input
              ref={searchRef}
              placeholder="Search anything…"
              aria-label="Search workspace"
              value={search}
              onFocus={() => setSearchOpen(true)}
              onChange={(e) => {
                setSearch(e.target.value);
                setSearchOpen(true);
              }}
            />
            <kbd>⌘ K</kbd>
            {searchOpen && (
              <div className="search-results">
                <small>QUICK NAVIGATION</small>
                {navigation
                  .filter((n) =>
                    n.page.toLowerCase().includes(search.toLowerCase()),
                  )
                  .slice(0, 5)
                  .map((n) => (
                    <button key={n.page} onClick={() => navigate(n.page)}>
                      <n.icon size={16} />
                      {n.page}
                      <ArrowRight size={13} />
                    </button>
                  ))}
                {found.length > 0 && <small>CALLS</small>}
                {found.map((c) => (
                  <button key={c.id} onClick={() => onCall(c)}>
                    <Phone size={16} />
                    {c.contact || c.number_masked}
                    <ArrowRight size={13} />
                  </button>
                ))}
                {search &&
                  !found.length &&
                  !navigation.some((n) =>
                    n.page.toLowerCase().includes(search.toLowerCase()),
                  ) && <p>No matches in this workspace.</p>}
              </div>
            )}
          </div>
          <div className="topbar-divider" />
          <div className="popover-anchor">
            <button
              className="icon-button notification-button"
              aria-label="Notifications"
              onClick={() => {
                setNotifications(!notifications);
                setAccount(false);
              }}
            >
              <Bell size={18} />
              {workspace.activity.length > 0 && <span />}
            </button>
            {notifications && (
              <div className="popover notifications">
                <h3>Workspace activity</h3>
                {workspace.activity.length ? (
                  workspace.activity.slice(0, 5).map((a, i) => (
                    <p key={a.id || i}>
                      <span className="activity-dot" />
                      {a.title || a.message || a.kind}
                    </p>
                  ))
                ) : (
                  <p>No new notifications.</p>
                )}
              </div>
            )}
          </div>
          <div className="topbar-divider" />
          <div className="popover-anchor">
            <button
              className="connection"
              onClick={() => {
                setAccount(!account);
                setNotifications(false);
              }}
            >
              <span className={`dot ${offline ? "red" : "green"}`} />
              <div>
                Local
                <small>
                  {offline ? "Reconnecting…" : `Connected to ${window.location.hostname}`}
                </small>
              </div>
              <ChevronDown size={14} />
            </button>
            {account && (
              <div className="popover account">
                <Button
                  onClick={() => {
                    navigate("Settings");
                    setAccount(false);
                  }}
                >
                  <SettingsIcon size={15} />
                  Workspace settings
                </Button>
                <Button onClick={logout}>
                  <LogOut size={15} />
                  Lock workspace
                </Button>
              </div>
            )}
          </div>
        </header>
        <div className="content">
          {workspace.health.demo && (
            <Alert>
              <strong>Synthetic demo workspace.</strong> All demo records are
              invented. No live provider is connected.
            </Alert>
          )}
          {offline && (
            <Alert error>
              Connection interrupted. Your last loaded data remains visible
              while the workspace reconnects.
            </Alert>
          )}
          {error && <Alert error>{error}</Alert>}
          {!data ? (
            <Loading />
          ) : (
            <>
              {displayed ? (
                <CallDetail
                  key={displayed.id}
                  call={displayed}
                  back={() => setSelected(null)}
                  guard={guard}
                  refresh={refresh}
                />
              ) : (
                <>
                  {page === "Overview" && (
                    <>
                      {workspace.health.gateway !== "connected" &&
                        !dismissed && (
                          <div className="gateway-alert">
                            <Alert>
                              <strong>Gateway connection unverified</strong>
                              <span>
                                {" "}
                                · Your local history is still available.
                              </span>
                            </Alert>
                            <button
                              className="icon-button"
                              aria-label="Dismiss gateway notice"
                              onClick={() => setDismissed(true)}
                            >
                              <X size={16} />
                            </button>
                          </div>
                        )}
                      <Overview
                        data={workspace}
                        navigate={navigate}
                        onCall={onCall}
                        prepare={() => setPrepare(true)}
                      />
                    </>
                  )}
                  {["Live Calls", "Call History", "Transcripts"].includes(
                    page,
                  ) && (
                    <CallsPage
                      key={page}
                      data={workspace}
                      live={page === "Live Calls"}
                      transcripts={page === "Transcripts"}
                      onCall={onCall}
                      prepare={() => setPrepare(true)}
                    />
                  )}
                  {page === "Agents" && (
                    <Registry
                      kind="agents"
                      data={workspace}
                      refresh={refresh}
                      onPrepare={() => setPrepare(true)}
                    />
                  )}
                  {page === "Contacts" && (
                    <Registry
                      kind="contacts"
                      data={workspace}
                      refresh={refresh}
                      onPrepare={() => setPrepare(true)}
                    />
                  )}
                  {page === "Providers & SIP" && (
                    <Registry
                      kind="providers"
                      data={workspace}
                      refresh={refresh}
                      onPrepare={() => setPrepare(true)}
                    />
                  )}
                  {page === "Automations" && (
                    <Registry
                      kind="tasks"
                      data={workspace}
                      refresh={refresh}
                      onPrepare={() => setPrepare(true)}
                    />
                  )}
                  {page === "Personal Vault" && (
                    <Vault data={workspace} guard={guard} />
                  )}
                  {page === "Analytics" && (
                    <Analytics data={workspace} guard={guard} />
                  )}
                  {page === "Live Logs" && <Logs data={workspace} />}
                  {page === "Messages" && <Messages />}
                  {page === "Settings" && (
                    <Settings
                      data={workspace}
                      theme={theme}
                      setTheme={setTheme}
                      refresh={refresh}
                    />
                  )}
                </>
              )}
            </>
          )}
          <footer>
            <span>Local by default. Yours by design.</span>
            <span>
              PhoneDex Control Centre <span className="version">v0.1.0</span>
            </span>
          </footer>
        </div>
      </main>
      {prepare && (
        <PrepareCall
          data={workspace}
          onClose={() => setPrepare(false)}
          guard={guard}
          refresh={refresh}
        />
      )}
      {guarded && (
        <Reauth
          reason={guarded.reason}
          onClose={() => setGuarded(null)}
          onSuccess={async () => {
            const action = guarded.action;
            setGuarded(null);
            try {
              await action();
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        />
      )}
    </div>
  );
}
function Reauth({
  reason,
  onClose,
  onSuccess,
}: {
  reason: string;
  onClose: () => void;
  onSuccess: () => Promise<void>;
}) {
  const [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <Modal title="Confirm it’s you" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await authenticate("auth/reauth", password);
            setPassword("");
            await onSuccess();
          } catch (e) {
            setError((e as Error).message);
            setBusy(false);
          }
        }}
      >
        <div className="reauth-icon">
          <ShieldCheck size={30} />
        </div>
        <p className="reauth-reason">{reason}</p>
        <p className="panel-description">
          Enter your operator password to authorize this sensitive action.
        </p>
        {error && <Alert error>{error}</Alert>}
        <Field label="Operator password">
          <input
            required
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy}>
            {busy ? "Verifying…" : "Confirm & continue"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
