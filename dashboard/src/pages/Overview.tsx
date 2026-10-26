import { useState } from "react";
import {
  Phone,
  ChartNoAxesColumnIncreasing,
  CircleCheck,
  Wallet,
  Activity,
  Server,
  Network,
  BrainCircuit,
  Database,
  Users,
  FileText,
  ArrowRight,
  Clock,
  SlidersHorizontal,
} from "lucide-react";
import {
  AreaChart,
  Area,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { Call, Workspace, Page } from "../types";
import {
  Panel,
  Empty,
  Button,
  Badge,
  date,
  duration,
  money,
  PageHeading,
} from "../ui";
export function CallsTable({
  calls,
  onCall,
}: {
  calls: Call[];
  onCall: (c: Call) => void;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Contact</th>
            <th>Direction</th>
            <th>Agent</th>
            <th>Duration</th>
            <th>Outcome</th>
            <th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr
              key={c.id}
              onClick={() => onCall(c)}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter") onCall(c);
              }}
            >
              <td>
                <strong>{c.contact || "Unknown contact"}</strong>
                <small>{c.number_masked}</small>
              </td>
              <td>
                <span className="direction">
                  <ArrowRight size={13} />
                  {c.direction || "outbound"}
                </span>
              </td>
              <td>{c.agent_name || "—"}</td>
              <td className="mono">{duration(c.duration_seconds)}</td>
              <td>
                <Badge
                  tone={
                    c.task_success === true
                      ? "green"
                      : c.state === "active"
                        ? "cyan"
                        : ""
                  }
                >
                  {c.outcome || c.state || "Unknown"}
                </Badge>
              </td>
              <td>
                {money(c.cost)}
                {c.cost != null &&
                  ["estimate", "estimated"].includes(c.cost_kind ?? "") && (
                    <small>Estimate</small>
                  )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!calls.length && (
        <Empty
          icon={<FileText size={30} />}
          title="A clean slate. Ready when you are."
          description="Your recent calls will appear here once PhoneDex is connected."
        />
      )}
    </div>
  );
}
export function Health({ health }: { health: Workspace["health"] }) {
  const items = [
    {
      icon: Server,
      label: "Gateway",
      value:
        health.gateway === "connected"
          ? "Connected"
          : health.gateway === "offline"
            ? "Offline"
            : "Unverified",
      tone:
        health.gateway === "connected"
          ? "green"
          : health.gateway === "offline"
            ? "red"
            : "",
    },
    {
      icon: Network,
      label: "SIP transport",
      value: health.sip_transport || "Unverified",
      tone: "",
    },
    {
      icon: BrainCircuit,
      label: "GPT Live",
      value: health.gpt_live || "Unverified",
      tone: "",
    },
    {
      icon: Database,
      label: "Transcript storage",
      value:
        health.transcript_storage === true || health.transcript_storage === "on"
          ? "On"
          : health.transcript_storage === false ||
              health.transcript_storage === "off"
            ? "Off"
            : "Unknown",
      tone: "",
    },
    { icon: Users, label: "Concurrency", value: "1 call", tone: "" },
  ];
  return (
    <Panel title="System health" icon={<Activity className="cyan" size={19} />}>
      <div className="health-list">
        {items.map((i) => (
          <div key={i.label}>
            <i.icon size={18} />
            <span>{i.label}</span>
            <span className="health-value">
              <span className={`dot ${i.tone}`} />
              {i.value}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
export function ActivityChart({
  calls,
  days = 1,
}: {
  calls: Call[];
  days?: number;
}) {
  const data = Array.from({ length: days === 1 ? 8 : days }, (_, i) => ({
    label: days === 1 ? `${String(i * 3).padStart(2, "0")}:00` : String(i + 1),
    calls: 0,
  }));
  calls.forEach((c) => {
    const time = new Date(c.started_at ?? "");
    if (isNaN(time.getTime())) return;
    const age = (Date.now() - time.getTime()) / 86400000;
    if (age <= days) {
      const bucket =
        days === 1
          ? Math.floor(time.getHours() / 3)
          : Math.max(0, days - 1 - Math.floor(age));
      if (data[bucket]) data[bucket].calls++;
    }
  });
  const has = data.some((d) => d.calls);
  return (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 15, right: 8, left: -25, bottom: 0 }}
        >
          <defs>
            <linearGradient id="cyan-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#54d9ef" stopOpacity={0.25} />
              <stop offset="1" stopColor="#54d9ef" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--grid)" vertical />
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--muted)", fontSize: 10 }}
          />
          <YAxis
            allowDecimals={false}
            domain={[0, has ? "auto" : 10]}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--muted)", fontSize: 10 }}
          />
          <Tooltip
            contentStyle={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 8,
            }}
          />
          <Area
            type="monotone"
            dataKey="calls"
            stroke={has ? "#54d9ef" : "transparent"}
            fill="url(#cyan-fill)"
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
      {!has && (
        <div className="chart-empty">
          <Phone size={28} />
          <strong>Your first call starts the story</strong>
          <p>Call activity will appear here once PhoneDex is connected.</p>
        </div>
      )}
    </div>
  );
}
export default function Overview({
  data,
  navigate,
  onCall,
  prepare,
}: {
  data: Workspace;
  navigate: (p: Page) => void;
  onCall: (c: Call) => void;
  prepare: () => void;
}) {
  const [range, setRange] = useState(1);
  const s = data.stats;
  const today = data.calls.filter(
    (c) =>
      new Date(c.started_at ?? "").toDateString() === new Date().toDateString(),
  ).length;
  const stats = [
    {
      label: "Active calls",
      value: s.active_calls ?? 0,
      hint:
        data.health.gateway === "connected"
          ? "Gateway connected"
          : "No gateway connected",
      icon: Phone,
      color: "blue",
    },
    {
      label: "Calls today",
      value: today,
      hint: today ? "Recorded in your workspace" : "No calls yet",
      icon: ChartNoAxesColumnIncreasing,
      color: "violet",
    },
    {
      label: "Task success",
      value: s.success_rate == null ? "—" : `${Math.round(s.success_rate)}%`,
      hint: s.known_outcomes
        ? `${s.known_outcomes} recorded outcomes`
        : "No recorded outcomes",
      icon: CircleCheck,
      color: "cyan",
    },
    {
      label: "Confirmed spend",
      value: money(s.total_cost),
      hint:
        s.estimated_cost != null
          ? `${money(s.estimated_cost)} estimated separately`
          : s.confirmed_cost_calls
            ? `${s.confirmed_cost_calls} confirmed charges`
            : "No confirmed charges",
      icon: Wallet,
      color: "violet",
    },
  ];
  return (
    <>
      <PageHeading
        title="Your call operations, in focus."
        description="A clear view of your self-hosted PhoneDex workspace."
        action={
          <>
            <Button onClick={() => navigate("Settings")}>
              <SlidersHorizontal size={16} />
              Diagnostics
            </Button>
            <Button variant="primary" onClick={prepare}>
              <Phone size={17} />
              Prepare a call
            </Button>
          </>
        }
      />
      <div className="stat-grid">
        {stats.map((s) => (
          <div className="stat" key={s.label}>
            <div className={`stat-icon ${s.color}`}>
              <s.icon size={22} />
            </div>
            <div>
              <span>{s.label}</span>
              <strong>{s.value}</strong>
              <small>{s.hint}</small>
            </div>
          </div>
        ))}
      </div>
      <div className="overview-top">
        <Panel
          title="Call activity"
          icon={<FileText size={18} />}
          action={
            <div className="segmented">
              {[
                [1, "24h"],
                [7, "7d"],
                [30, "30d"],
              ].map(([v, l]) => (
                <button
                  key={v}
                  onClick={() => setRange(Number(v))}
                  className={range === v ? "selected" : ""}
                >
                  {l}
                </button>
              ))}
            </div>
          }
        >
          <ActivityChart calls={data.calls} days={range} />
        </Panel>
        <Health health={data.health} />
      </div>
      <div className="overview-bottom">
        <Panel
          title="Recent calls"
          icon={<FileText className="violet" size={18} />}
          action={
            <button
              className="text-button"
              onClick={() => navigate("Call History")}
            >
              View all
              <ArrowRight size={14} />
            </button>
          }
        >
          <CallsTable calls={data.calls.slice(0, 5)} onCall={onCall} />
        </Panel>
        <Panel
          title="Recent activity"
          icon={<Clock className="violet" size={19} />}
          action={
            <button
              className="text-button"
              onClick={() => navigate("Live Logs")}
            >
              View all
              <ArrowRight size={14} />
            </button>
          }
        >
          <div className="activity-list">
            {data.activity.length ? (
              data.activity.slice(0, 5).map((a, i) => (
                <div key={a.id || i}>
                  <span className="activity-dot" />
                  <div>
                    <strong>{a.title || a.message || a.kind}</strong>
                    <p>{a.description || a.detail || ""}</p>
                  </div>
                  <small>{date(a.at || a.created_at || a.timestamp)}</small>
                </div>
              ))
            ) : (
              <div>
                <span className="activity-dot" />
                <div>
                  <strong>Control Centre ready</strong>
                  <p>Local workspace initialized.</p>
                </div>
              </div>
            )}
          </div>
        </Panel>
      </div>
    </>
  );
}
