import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, LineChart, Line, PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import { getObservabilitySummary, getTokenDaily, getLatency, getErrors, ObservabilitySummary, DailyToken, LatencyData, ErrorGroup } from "../api";

const COLORS = ["#6C5CE7", "#00D2FF", "#00E676", "#FFD600", "#FF5252", "#448AFF"];

export default function Dashboard({ wsId }: { wsId: string }) {
  const [summary, setSummary] = useState<ObservabilitySummary | null>(null);
  const [tokens, setTokens] = useState<DailyToken[]>([]);
  const [latency, setLatency] = useState<LatencyData | null>(null);
  const [errors, setErrors] = useState<ErrorGroup[]>([]);

  useEffect(() => {
    getObservabilitySummary(wsId).then(setSummary);
    getTokenDaily(wsId).then(setTokens);
    getLatency(wsId).then(setLatency);
    getErrors(wsId).then(setErrors);
  }, [wsId]);

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
              <CartesianGrid strokeDasharray="3 3" stroke="#2A2A3E" />
              <XAxis dataKey="date" tick={{ fill: "#8888A0", fontSize: 12 }} />
              <YAxis tick={{ fill: "#8888A0", fontSize: 12 }} />
              <Tooltip
                contentStyle={{ background: "#1A1A2E", border: "1px solid #2A2A3E", borderRadius: 8, color: "#E8E8F0" }}
              />
              <Bar dataKey="input_tokens" fill="#6C5CE7" name="Input" radius={[4, 4, 0, 0]} />
              <Bar dataKey="output_tokens" fill="#00D2FF" name="Output" radius={[4, 4, 0, 0]} />
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
                <CartesianGrid strokeDasharray="3 3" stroke="#2A2A3E" />
                <XAxis dataKey="bucket" tick={{ fill: "#8888A0", fontSize: 12 }} />
                <YAxis tick={{ fill: "#8888A0", fontSize: 12 }} />
                <Tooltip
                  contentStyle={{ background: "#1A1A2E", border: "1px solid #2A2A3E", borderRadius: 8, color: "#E8E8F0" }}
                />
                <Line type="monotone" dataKey="p50" stroke="#6C5CE7" name="p50" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="p95" stroke="#00D2FF" name="p95" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="p99" stroke="#FF5252" name="p99" strokeWidth={2} dot={false} />
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
                  labelLine={{ stroke: "#2A2A3E" }}
                >
                  {errors.map((_, idx) => <Cell key={idx} fill={COLORS[idx % COLORS.length]} />)}
                </Pie>
                <Tooltip
                  contentStyle={{ background: "#1A1A2E", border: "1px solid #2A2A3E", borderRadius: 8, color: "#E8E8F0" }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}