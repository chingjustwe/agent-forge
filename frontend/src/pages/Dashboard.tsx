import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, LineChart, Line, PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import { getObservabilitySummary, getTokenDaily, getLatency, getErrors, ObservabilitySummary, DailyToken, LatencyData, ErrorGroup } from "../api";

const COLORS = ["#0088FE", "#00C49F", "#FFBB28", "#FF8042"];

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
    <div style={{ padding: 24 }}>
      <h1>Dashboard</h1>

      {summary && (
        <div style={{ display: "flex", gap: 16, marginBottom: 24 }}>
          <Card label="Total Requests" value={summary.total_requests} />
          <Card label="Avg Latency (ms)" value={summary.avg_latency_ms.toFixed(2)} />
          <Card label="Total Tokens" value={summary.total_tokens} />
          <Card label="Error Rate" value={(summary.error_rate * 100).toFixed(2) + "%"} />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
        <div style={{ background: "#fff", padding: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)" }}>
          <h3>Token Usage (Daily)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={tokens}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="input_tokens" fill="#0088FE" name="Input" />
              <Bar dataKey="output_tokens" fill="#00C49F" name="Output" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {latency && (
          <div style={{ background: "#fff", padding: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)" }}>
            <h3>Latency (ms)</h3>
            <p>p50: {latency.p50_ms}ms | p95: {latency.p95_ms}ms | p99: {latency.p99_ms}ms</p>
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={latency.over_time}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="bucket" />
                <YAxis />
                <Tooltip />
                <Line type="monotone" dataKey="p50" stroke="#0088FE" name="p50" />
                <Line type="monotone" dataKey="p95" stroke="#FF8042" name="p95" />
                <Line type="monotone" dataKey="p99" stroke="#FF0000" name="p99" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {errors.length > 0 && (
          <div style={{ background: "#fff", padding: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)" }}>
            <h3>Error Breakdown</h3>
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie data={errors} dataKey="count" nameKey="error_type" cx="50%" cy="50%" outerRadius={80} label>
                  {errors.map((_, idx) => <Cell key={idx} fill={COLORS[idx % COLORS.length]} />)}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}

function Card({ label, value }: { label: string; value: string | number }) {
  return (
    <div style={{ flex: 1, background: "#fff", padding: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)", textAlign: "center" }}>
      <div style={{ fontSize: 12, color: "#666" }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: "bold" }}>{value}</div>
    </div>
  );
}
