import { useEffect, useState } from "react";
import { Activity, Download, ShieldCheck } from "lucide-react";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { Stats, Workspace } from "../types";
import type { Guard } from "./Calls";
import { api, download } from "../api";
import {
  Alert,
  Button,
  Empty,
  PageHeading,
  Panel,
  money,
  duration,
} from "../ui";
interface AnalyticsResponse extends Stats {
  successful_tasks: number;
  failed_tasks: number;
  unknown_outcomes: number;
  unknown_date_calls: number;
  known_duration_calls: number;
  cost_per_completed_task: number | null;
  average_confirmed_cost: number | null;
  average_estimated_cost: number | null;
  by_hour: { hour: number; calls: number }[];
  by_agent: (Stats & { agent_id: string | null; agent_name: string })[];
  outcomes: { outcome: string; count: number }[];
  states: { state: string; count: number }[];
  errors: { code: string; count: number }[];
  duration_distribution: {
    min_seconds: number;
    max_seconds: number | null;
    count: number;
  }[];
  timezone: string;
}
interface Datum {
  name: string;
  value: number;
}
function Bars({ data, label }: { data: Datum[]; label: string }) {
  return data.some((d) => d.value) ? (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          margin={{ top: 15, right: 22, left: -18, bottom: 0 }}
        >
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="name" tick={{ fill: "var(--muted)", fontSize: 10 }} />
          <YAxis
            allowDecimals={false}
            tick={{ fill: "var(--muted)", fontSize: 10 }}
          />
          <Tooltip
            contentStyle={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 7,
            }}
          />
          <Bar
            name={label}
            dataKey="value"
            fill="var(--cyan)"
            radius={[4, 4, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  ) : (
    <Empty
      title="No recorded data for this period"
      description="Unknown measurements stay unknown until the core records them."
    />
  );
}
export default function Analytics({
  data,
  guard,
}: {
  data: Workspace;
  guard: Guard;
}) {
  const [range, setRange] = useState(30),
    [tab, setTab] = useState("Volume"),
    [report, setReport] = useState<AnalyticsResponse | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let valid = true;
    api<AnalyticsResponse>(`analytics?days=${range}`)
      .then((v) => {
        if (valid) setReport(v);
      })
      .catch((e) => {
        if (valid) setError(e.message);
      });
    return () => {
      valid = false;
    };
  }, [range, data.calls]);
  const s = report || data.stats;
  const metric = [
    ["Total calls", s.total_calls ?? 0],
    [
      "Task success",
      s.success_rate == null ? "—" : `${Math.round(s.success_rate)}%`,
    ],
    ["Average duration", duration(s.average_duration)],
    ["Confirmed spend", money(s.total_cost)],
  ];
  const chartData: Datum[] = !report
    ? []
    : tab === "Agents"
      ? report.by_agent.map((a) => ({
          name: a.agent_name,
          value: a.total_calls || 0,
        }))
      : tab === "Outcomes"
        ? report.outcomes.map((a) => ({ name: a.outcome, value: a.count }))
        : tab === "Duration"
          ? report.duration_distribution.map((a) => ({
              name:
                a.max_seconds == null
                  ? `${a.min_seconds}s+`
                  : `${a.min_seconds}–${a.max_seconds}s`,
              value: a.count,
            }))
          : tab === "Errors"
            ? report.errors.map((a) => ({ name: a.code, value: a.count }))
            : tab === "Time of day"
              ? report.by_hour.map((a) => ({
                  name: `${String(a.hour).padStart(2, "0")}:00`,
                  value: a.calls,
                }))
              : [];
  return (
    <>
      <PageHeading
        title="See the pattern. Find the signal."
        description="A transparent view of recorded activity, explicit outcomes and known costs."
        action={
          <>
            <select
              aria-label="Analytics period"
              value={range}
              onChange={(e) => setRange(Number(e.target.value))}
            >
              {[7, 30, 90].map((d) => (
                <option key={d} value={d}>
                  Last {d} days
                </option>
              ))}
            </select>
            <Button
              onClick={() =>
                guard(async () => {
                  try {
                    await download("export?format=csv", "phonedex-calls.csv");
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }, "Export call analytics")
              }
            >
              <Download size={15} />
              Export CSV
            </Button>
          </>
        }
      />
      {error && <Alert error>{error}</Alert>}
      <div className="stat-grid analytics-stats">
        {metric.map(([key, value]) => (
          <div className="stat" key={key}>
            <div>
              <span>{key}</span>
              <strong>{value}</strong>
              <small>
                {key === "Task success"
                  ? `${s.known_outcomes ?? 0} explicit outcomes`
                  : key === "Confirmed spend"
                    ? `${s.confirmed_cost_calls ?? 0} confirmed charges`
                    : key === "Average duration"
                      ? `${report?.known_duration_calls ?? 0} known durations`
                      : "Recorded workspace data"}
              </small>
            </div>
          </div>
        ))}
      </div>
      <Panel title="Call analytics" icon={<Activity size={18} />}>
        <div className="tabs">
          {[
            "Volume",
            "Time of day",
            "Agents",
            "Outcomes",
            "Duration",
            "Costs",
            "Errors",
          ].map((t) => (
            <button
              key={t}
              className={tab === t ? "selected" : ""}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>
        {tab === "Volume" ? (
          report?.daily?.some((d) => d.calls) ? (
            <div className="chart-frame">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={report.daily}
                  margin={{ top: 15, right: 20, left: -18, bottom: 0 }}
                >
                  <defs>
                    <linearGradient
                      id="analytics-cyan"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="0"
                        stopColor="var(--cyan)"
                        stopOpacity={0.3}
                      />
                      <stop
                        offset="1"
                        stopColor="var(--cyan)"
                        stopOpacity={0}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--grid)" />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(s) => s.slice(5)}
                    tick={{ fill: "var(--muted)", fontSize: 10 }}
                    minTickGap={30}
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={{ fill: "var(--muted)", fontSize: 10 }}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "var(--panel)",
                      border: "1px solid var(--border)",
                      borderRadius: 7,
                    }}
                  />
                  <Area
                    dataKey="calls"
                    name="Calls"
                    type="monotone"
                    stroke="var(--cyan)"
                    fill="url(#analytics-cyan)"
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty
              title="Your first call starts the story"
              description="Recorded call volume will appear here."
            />
          )
        ) : tab === "Costs" ? (
          <div className="cost-report">
            <div>
              <small>Confirmed charges</small>
              <strong>{money(s.total_cost)}</strong>
              <p>
                {s.confirmed_cost_calls ?? 0} calls · average{" "}
                {money(report?.average_confirmed_cost)}
              </p>
            </div>
            <div>
              <small>Provider estimates</small>
              <strong>{money(s.estimated_cost)}</strong>
              <p>
                {s.estimated_cost_calls ?? 0} calls · average{" "}
                {money(report?.average_estimated_cost)}
              </p>
            </div>
            <div>
              <small>Unknown cost</small>
              <strong>{s.unknown_cost_calls ?? 0}</strong>
              <p>Calls without verified cost classification</p>
            </div>
            <div>
              <small>Cost per successful task</small>
              <strong>{money(report?.cost_per_completed_task)}</strong>
              <p>Requires complete confirmed costs and explicit outcomes</p>
            </div>
          </div>
        ) : (
          <Bars
            data={chartData}
            label={tab === "Duration" ? "Calls" : "Count"}
          />
        )}
      </Panel>
      <div className="analytics-note">
        <ShieldCheck size={16} />
        <span>
          Task success comes from explicit outcomes. Estimates stay separate
          from confirmed charges. Time buckets use UTC.
          {report?.unknown_date_calls
            ? ` ${report.unknown_date_calls} undated calls are excluded from charts.`
            : ""}
        </span>
      </div>
    </>
  );
}
