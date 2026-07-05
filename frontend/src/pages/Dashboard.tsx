import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, LineChart, Line, PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import { getObservabilitySummary, getTokenDaily, getLatency, getErrors, ObservabilitySummary, DailyToken, LatencyData, ErrorGroup } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";

// New design-system color palette for charts
const CHART_COLORS = ["#2563ef", "#3a81f6", "#91c5ff", "#f59e0b", "#ef4444", "#8b5cf6"];
// Recharts-compatible hardcoded dark colors
const CHART_TEXT = "#fafafa";
const CHART_MUTED = "#737373";
const CHART_GRID = "#262626";
const CHART_TOOLTIP_STYLE = {
  background: "#1a1a1a",
  border: "1px solid #333",
  borderRadius: 8,
  color: CHART_TEXT,
};

export default function Dashboard() {
  const { currentWorkspaceId } = useWorkspace();
  const [summary, setSummary] = useState<ObservabilitySummary | null>(null);
  const [tokens, setTokens] = useState<DailyToken[]>([]);
  const [latency, setLatency] = useState<LatencyData | null>(null);
  const [errors, setErrors] = useState<ErrorGroup[]>([]);

  useEffect(() => {
    if (!currentWorkspaceId) return;
    getObservabilitySummary(currentWorkspaceId).then(setSummary);
    getTokenDaily(currentWorkspaceId).then(setTokens);
    getLatency(currentWorkspaceId).then(setLatency);
    getErrors(currentWorkspaceId).then(setErrors);
  }, [currentWorkspaceId]);

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Dashboard</h1>
          <p className="page-subtitle">Observability and usage metrics for your workspace</p>
        </div>
        <div className="alert alert-info">No workspace selected. Please select a workspace from the top bar.</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <p className="page-subtitle">Observability and usage metrics for your workspace</p>
      </div>

      {summary && (
        <div className="stat-grid">
          <div className="stat-card stat-card-accent">
            <div className="stat-card-value">{summary.total_requests}</div>
            <div className="stat-card-label">Total Requests</div>
          </div>
          <div className="stat-card stat-card-accent-success">
            <div className="stat-card-value">{summary.avg_latency_ms.toFixed(2)}</div>
            <div className="stat-card-label">Avg Latency (ms)</div>
          </div>
          <div className="stat-card stat-card-accent">
            <div className="stat-card-value">{summary.total_tokens.toLocaleString()}</div>
            <div className="stat-card-label">Total Tokens</div>
          </div>
          <div className="stat-card stat-card-accent-warning">
            <div className="stat-card-value">{(summary.error_rate * 100).toFixed(2)}%</div>
            <div className="stat-card-label">Error Rate</div>
          </div>
        </div>
      )}

      <div className="chart-grid">
        <div className="chart-card">
          <h3 className="chart-card-title">Token Usage (Daily)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={tokens}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
              <XAxis dataKey="date" tick={{ fill: CHART_MUTED, fontSize: 12 }} />
              <YAxis tick={{ fill: CHART_MUTED, fontSize: 12 }} />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              <Bar dataKey="input_tokens" fill="#2563ef" name="Input" radius={[4, 4, 0, 0]} />
              <Bar dataKey="output_tokens" fill="#3a81f6" name="Output" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {latency && (
          <div className="chart-card">
            <h3 className="chart-card-title">Latency (ms)</h3>
            <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: 12 }}>
              p50: {latency.p50_ms}ms &middot; p95: {latency.p95_ms}ms &middot; p99: {latency.p99_ms}ms
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={latency.over_time}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                <XAxis dataKey="bucket" tick={{ fill: CHART_MUTED, fontSize: 12 }} />
                <YAxis tick={{ fill: CHART_MUTED, fontSize: 12 }} />
                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                <Line type="monotone" dataKey="p50" stroke="#2563ef" name="p50" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="p95" stroke="#3a81f6" name="p95" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="p99" stroke="#ef4444" name="p99" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {errors.length > 0 && (
          <div className="chart-card">
            <h3 className="chart-card-title">Error Breakdown</h3>
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie
                  data={errors}
                  dataKey="count"
                  nameKey="error_type"
                  cx="50%"
                  cy="50%"
                  outerRadius={90}
                  label={({ error_type, count }) => `${error_type} (${count})`}
                  labelLine={{ stroke: CHART_GRID }}
                >
                  {errors.map((_, idx) => <Cell key={idx} fill={CHART_COLORS[idx % CHART_COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
