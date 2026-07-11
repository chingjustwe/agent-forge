import { useEffect, useState, useCallback } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, LineChart, Line, PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import {
  getObservabilitySummary, getTokenDaily, getLatency, getErrors,
  fetchUsage, updateQuota, getQuota,
  ObservabilitySummary, DailyToken, LatencyData, ErrorGroup,
  UsageData, QuotaInfo,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { useToast } from "../components/Toast";
import { Modal } from "../components/Modal";
import { EmptyState } from "../components/EmptyState";
import { SkeletonTable, SkeletonText } from "../components/Skeleton";
import { DatePicker } from "../components/DatePicker";

// Chart colors (hardcoded for recharts dark-theme rendering)
const CHART_COLORS = ["#2563ef", "#3a81f6", "#91c5ff", "#f59e0b", "#ef4444", "#8b5cf6"];
const CHART_MUTED = "#737373";
const CHART_GRID = "#262626";
const CHART_TOOLTIP_STYLE = {
  background: "#1a1a1a",
  border: "1px solid #333",
  borderRadius: 8,
  color: "#fafafa",
};

type RangePreset = "today" | "7d" | "30d" | "all";

function toISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function applyPreset(preset: RangePreset): { since: string; until: string } {
  const today = new Date();
  if (preset === "today") {
    const iso = toISO(today);
    return { since: iso, until: iso };
  }
  if (preset === "7d") {
    const start = new Date(today);
    start.setDate(start.getDate() - 6);
    return { since: toISO(start), until: toISO(today) };
  }
  if (preset === "30d") {
    const start = new Date(today);
    start.setDate(start.getDate() - 29);
    return { since: toISO(start), until: toISO(today) };
  }
  return { since: "", until: "" };
}

const PRESET_LABELS: { value: RangePreset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 Days" },
  { value: "30d", label: "30 Days" },
  { value: "all", label: "All" },
];

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

export default function Analytics() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const toast = useToast();

  // Data state
  const [summary, setSummary] = useState<ObservabilitySummary | null>(null);
  const [tokens, setTokens] = useState<DailyToken[]>([]);
  const [latency, setLatency] = useState<LatencyData | null>(null);
  const [errors, setErrors] = useState<ErrorGroup[]>([]);
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);

  // Filters
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [activePreset, setActivePreset] = useState<RangePreset | null>("today");

  // Quota edit modal
  const [editing, setEditing] = useState<string | null>(null);
  const [editTokens, setEditTokens] = useState(0);
  const [editCost, setEditCost] = useState(0);
  const [saving, setSaving] = useState(false);

  // Admin 判断：workspace_admin 或 tenant_admin 看 workspace 全量数据
  const isAdmin = currentRole === "workspace_admin" || currentRole === "tenant_admin";
  // Per-workspace 表格仅 tenant_admin 可见（跨 workspace 视图）
  const showPerWorkspaceTable = currentRole === "tenant_admin";
  const canEditQuota = currentRole === "workspace_admin" || currentRole === "tenant_admin";

  // 视角切换：admin 可在 Workspace / My Activity 之间切换；member 只能看自己
  const [scope, setScope] = useState<"workspace" | "me">("workspace");
  const userIdParam = (!isAdmin || scope === "me") ? "me" : undefined;

  // Default to "Today"
  useEffect(() => {
    const { since: s, until: u } = applyPreset("today");
    setSince(s);
    setUntil(u);
  }, []);

  const load = useCallback(() => {
    if (!currentWorkspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    // 所有用户都调用 observability 端点（后端按角色自动过滤 user_id）
    const sinceParam = since || undefined;
    const untilParam = until || undefined;
    Promise.all([
      getObservabilitySummary(currentWorkspaceId, userIdParam, sinceParam),
      getTokenDaily(currentWorkspaceId, sinceParam, untilParam, userIdParam),
      getLatency(currentWorkspaceId, sinceParam, untilParam, userIdParam),
      getErrors(currentWorkspaceId, sinceParam, userIdParam),
      getQuota(currentWorkspaceId),
    ]).then(([s, t, l, e, q]) => {
      setSummary(s);
      setTokens(t);
      setLatency(l);
      setErrors(e);
      setQuota(q);
    }).finally(() => setLoading(false));

    // admin 额外获取 per-workspace usage 表格（仅 workspace scope 时显示）
    if (showPerWorkspaceTable && scope === "workspace") {
      fetchUsage({ since: sinceParam, until: untilParam }).then(setUsage).catch(() => {});
    }
  }, [currentWorkspaceId, since, until, showPerWorkspaceTable, userIdParam, scope]);

  useEffect(() => {
    if (since || until) load();
  }, [load]); // eslint-disable-line react-hooks/exhaustive-deps

  const handlePreset = (preset: RangePreset) => {
    setActivePreset(preset);
    const { since: s, until: u } = applyPreset(preset);
    setSince(s);
    setUntil(u);
  };

  const handleSinceChange = (v: string) => {
    setSince(v);
    setActivePreset(null);
  };
  const handleUntilChange = (v: string) => {
    setUntil(v);
    setActivePreset(null);
  };

  const openEditModal = (wsId: string, maxTokens: number, maxCost: number) => {
    setEditing(wsId);
    setEditTokens(maxTokens);
    setEditCost(maxCost);
  };

  const handleSave = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await updateQuota(editing, {
        max_tokens_per_day: editTokens,
        max_cost_per_month: editCost,
      });
      setEditing(null);
      toast.success("Quota updated", "Limits have been saved successfully.");
      load();
    } catch (err) {
      toast.error("Save failed", err instanceof Error ? err.message : "Failed to update quota");
    } finally {
      setSaving(false);
    }
  };

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Analytics</h1>
          <p className="page-subtitle">
            {isAdmin ? "Workspace usage, cost & performance analytics" : "Your usage, cost & performance analytics"}
          </p>
        </div>
        <div className="alert alert-info">No workspace selected. Please select a workspace from the top bar.</div>
      </div>
    );
  }

  // Token split proportions
  const totalTokens = summary?.total_tokens ?? 0;
  const inputPct = totalTokens > 0 ? (summary!.input_tokens / totalTokens) * 100 : 0;
  const outputPct = totalTokens > 0 ? (summary!.output_tokens / totalTokens) * 100 : 0;

  // Quota progress
  const quotaPct = quota && quota.max_tokens_per_day > 0
    ? Math.min(100, (quota.tokens_used / quota.max_tokens_per_day) * 100)
    : 0;
  const barClass = quotaPct > 90 ? "progress-bar-fill-error" : quotaPct > 70 ? "progress-bar-fill-warning" : "";

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="page-title">Analytics</h1>
          <p className="page-subtitle">
            {isAdmin
              ? (scope === "workspace" ? "Workspace usage, cost & performance analytics" : "Your personal activity analytics")
              : "Your usage, cost & performance analytics"}
          </p>
        </div>
        {isAdmin && (
          <div className="range-presets" style={{ flexShrink: 0 }}>
            <button
              className={`btn btn-sm ${scope === "workspace" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setScope("workspace")}
            >
              Workspace
            </button>
            <button
              className={`btn btn-sm ${scope === "me" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setScope("me")}
            >
              My Activity
            </button>
          </div>
        )}
      </div>

      {/* Time range selector only — scope toggle moved to header */}
      <div className="filter-bar">
        <div className="range-presets">
          {PRESET_LABELS.map(p => (
            <button
              key={p.value}
              className={`btn btn-sm ${activePreset === p.value ? "btn-primary" : "btn-secondary"}`}
              onClick={() => handlePreset(p.value)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <label style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>From:</label>
        <DatePicker value={since} onChange={handleSinceChange} placeholder="Start date" max={until || undefined} />
        <label style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>To:</label>
        <DatePicker value={until} onChange={handleUntilChange} placeholder="End date" min={since || undefined} />
        <button className="btn btn-secondary" onClick={load}>Apply</button>
      </div>

      {loading ? (
        <>
          <div className="stat-grid">
            {[1, 2, 3].map(i => (
              <div key={i} className="stat-card">
                <SkeletonText lines={2} />
              </div>
            ))}
          </div>
          <div className="stat-grid">
            {[4, 5, 6].map(i => (
              <div key={i} className="stat-card">
                <SkeletonText lines={2} />
              </div>
            ))}
          </div>
          <SkeletonTable rows={5} cols={7} />
        </>
      ) : summary && summary.total_requests > 0 ? (
        <>
          {/* Row 1: Input / Output / Cost */}
          <div className="stat-grid">
            <div className="stat-card">
              <div className="stat-card-value">{formatTokens(summary.input_tokens)}</div>
              <div className="stat-card-label">Input Tokens</div>
            </div>
            <div className="stat-card">
              <div className="stat-card-value">{formatTokens(summary.output_tokens)}</div>
              <div className="stat-card-label">Output Tokens</div>
            </div>
            <div className="stat-card stat-card-accent-warning">
              <div className="stat-card-value">${summary.total_cost.toFixed(2)}</div>
              <div className="stat-card-label">Total Cost</div>
            </div>
          </div>
          {/* Row 2: Requests / Latency / Error Rate */}
          <div className="stat-grid">
            <div className="stat-card stat-card-accent">
              <div className="stat-card-value">{summary.total_requests.toLocaleString()}</div>
              <div className="stat-card-label">Total Requests</div>
            </div>
            <div className="stat-card stat-card-accent-success">
              <div className="stat-card-value">{summary.avg_latency_ms.toFixed(2)}</div>
              <div className="stat-card-label">Avg Latency (ms)</div>
            </div>
            <div className="stat-card stat-card-accent">
              <div className="stat-card-value">{(summary.error_rate * 100).toFixed(2)}%</div>
              <div className="stat-card-label">Error Rate</div>
            </div>
          </div>

          {/* Token distribution bar */}
          {totalTokens > 0 && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div className="card-header">
                <h3 className="card-title">Token Distribution</h3>
              </div>
              <div style={{ display: "flex", height: 12, borderRadius: 100, overflow: "hidden", backgroundColor: "var(--bg-elevated)" }}>
                <div
                  style={{ width: `${inputPct}%`, backgroundColor: "#2563ef", transition: "width 0.4s ease" }}
                  title={`Input: ${formatTokens(summary.input_tokens)} (${inputPct.toFixed(1)}%)`}
                />
                <div
                  style={{ width: `${outputPct}%`, backgroundColor: "#3a81f6", transition: "width 0.4s ease" }}
                  title={`Output: ${formatTokens(summary.output_tokens)} (${outputPct.toFixed(1)}%)`}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: "0.82rem" }}>
                <span style={{ color: "var(--text-secondary)" }}>
                  <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, backgroundColor: "#2563ef", marginRight: 6, verticalAlign: "middle" }} />
                  Input: <strong style={{ color: "var(--text-primary)" }}>{formatTokens(summary.input_tokens)}</strong> ({inputPct.toFixed(1)}%)
                </span>
                <span style={{ color: "var(--text-secondary)" }}>
                  <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, backgroundColor: "#3a81f6", marginRight: 6, verticalAlign: "middle" }} />
                  Output: <strong style={{ color: "var(--text-primary)" }}>{formatTokens(summary.output_tokens)}</strong> ({outputPct.toFixed(1)}%)
                </span>
              </div>
            </div>
          )}

          {/* Quota management — admin only, workspace scope only */}
          {canEditQuota && quota && scope === "workspace" && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div className="card-header">
                <h3 className="card-title">Workspace Quota — Today</h3>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => openEditModal(currentWorkspaceId, quota.max_tokens_per_day, quota.max_cost_per_month)}
                >
                  Edit Limits
                </button>
              </div>
              <div className="progress-bar">
                <div className={`progress-bar-fill ${barClass}`} style={{ width: `${quotaPct}%` }} />
              </div>
              <p className="quota-usage-text">
                {quota.tokens_used.toLocaleString()} /{" "}
                {quota.max_tokens_per_day === 0 ? "Unlimited" : quota.max_tokens_per_day.toLocaleString()} tokens today
                {" · "}Cost today: ${quota.cost_today.toFixed(4)}
                {" · "}Max cost/month: ${quota.max_cost_per_month.toFixed(2)}
              </p>
            </div>
          )}

          {/* Per-workspace breakdown — tenant_admin only, workspace scope only */}
          {showPerWorkspaceTable && usage && usage.by_workspace.length > 0 && scope === "workspace" && (
            <div className="detail-section">
              <h2 className="detail-section-title">Per-Workspace Breakdown</h2>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Workspace</th>
                      <th>Requests</th>
                      <th>Input</th>
                      <th>Output</th>
                      <th>Total Tokens</th>
                      <th>Cost</th>
                      <th>Daily Limit</th>
                      <th>Usage %</th>
                      {canEditQuota && <th>Actions</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {usage.by_workspace.map((ws) => {
                      const pct = ws.max_tokens_per_day > 0
                        ? Math.min(100, (ws.tokens_used_today / ws.max_tokens_per_day) * 100)
                        : 0;
                      const rowBarClass = pct > 90 ? "progress-bar-fill-error" : pct > 70 ? "progress-bar-fill-warning" : "";
                      return (
                        <tr key={ws.workspace_id}>
                          <td>{ws.name}</td>
                          <td>{ws.total_requests.toLocaleString()}</td>
                          <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{formatTokens(ws.input_tokens)}</td>
                          <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{formatTokens(ws.output_tokens)}</td>
                          <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{formatTokens(ws.total_tokens)}</td>
                          <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>${ws.total_cost.toFixed(2)}</td>
                          <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>
                            {ws.max_tokens_per_day === 0 ? "Unlimited" : formatTokens(ws.max_tokens_per_day)}
                          </td>
                          <td>
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <div className="progress-bar" style={{ flex: 1, minWidth: 60, height: 8 }}>
                                <div className={`progress-bar-fill ${rowBarClass}`} style={{ width: `${pct}%` }} />
                              </div>
                              <span style={{ fontSize: "0.82rem", fontFamily: "var(--font-mono)" }}>{pct.toFixed(0)}%</span>
                            </div>
                          </td>
                          {canEditQuota && (
                            <td>
                              <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => openEditModal(ws.workspace_id, ws.max_tokens_per_day, ws.max_cost_per_month)}
                              >
                                Edit
                              </button>
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Trend charts */}
          <div className="chart-grid">
            {tokens.length > 0 && (
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
            )}

            {latency && (
              <div className="chart-card">
                <h3 className="chart-card-title">Latency (ms)</h3>
                <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: 12 }}>
                  p50: {latency.p50_ms}ms &middot; p95: {latency.p95_ms}ms &middot; p99: {latency.p99_ms}ms
                </div>
                {latency.over_time.length > 0 ? (
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
                ) : (
                  <div style={{ height: 100, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
                    No latency data for the selected period
                  </div>
                )}
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
        </>
      ) : (
        <EmptyState
          title="No analytics data"
          description="No data is available for the selected period."
        />
      )}

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="Edit Quota Limits"
        width="sm"
        footer={
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn btn-secondary" onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        }
      >
        <div className="form-group">
          <label className="form-label">Max Tokens Per Day</label>
          <input
            type="number"
            value={editTokens}
            onChange={e => setEditTokens(Number(e.target.value))}
          />
        </div>
        <div className="form-group">
          <label className="form-label">Max Cost Per Month (USD)</label>
          <input
            type="number"
            step="0.01"
            value={editCost}
            onChange={e => setEditCost(Number(e.target.value))}
          />
        </div>
      </Modal>
    </div>
  );
}
