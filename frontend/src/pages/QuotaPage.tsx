import { useEffect, useState } from "react";
import { getQuota, updateQuota, QuotaInfo } from "../api";

export default function QuotaPage({ wsId, isAdmin }: { wsId: string; isAdmin: boolean }) {
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const [editTokens, setEditTokens] = useState(0);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    getQuota(wsId).then(data => {
      setQuota(data);
      setEditTokens(data.max_tokens_per_day);
    });
  }, [wsId]);

  const handleSave = async () => {
    await updateQuota(wsId, { max_tokens_per_day: editTokens });
    setEditing(false);
    getQuota(wsId).then(setQuota);
  };

  if (!quota) return <div className="loading">Loading quota data</div>;

  const pct = quota.max_tokens_per_day > 0
    ? Math.min(100, (quota.tokens_used / quota.max_tokens_per_day) * 100)
    : 0;

  const barClass = pct > 90 ? "progress-bar-fill-error" : pct > 70 ? "progress-bar-fill-warning" : "";

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Quota Management</h1>
        <p className="page-subtitle">Monitor and configure token usage limits</p>
      </div>

      <div className="stat-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="stat-card stat-card-accent">
          <div className="stat-card-value">{quota.tokens_used.toLocaleString()}</div>
          <div className="stat-card-label">Tokens Used Today</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">${quota.cost_today.toFixed(4)}</div>
          <div className="stat-card-label">Cost Today</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header">
          <h3 className="card-title">Today's Usage</h3>
        </div>
        <div className="progress-bar">
          <div
            className={`progress-bar-fill ${barClass}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="quota-usage-text">
          {quota.tokens_used.toLocaleString()} / {quota.max_tokens_per_day === 0 ? "Unlimited" : quota.max_tokens_per_day.toLocaleString()} tokens
        </p>
      </div>

      {isAdmin && (
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Configuration</h3>
          </div>
          {editing ? (
            <div>
              <div className="form-group">
                <label className="form-label">Max Tokens Per Day</label>
                <input
                  type="number"
                  value={editTokens}
                  onChange={e => setEditTokens(Number(e.target.value))}
                />
              </div>
              <div className="btn-group">
                <button className="btn btn-primary" onClick={handleSave}>Save</button>
                <button className="btn btn-secondary" onClick={() => setEditing(false)}>Cancel</button>
              </div>
            </div>
          ) : (
            <div className="quota-config">
              <p>Max tokens/day: <strong>{quota.max_tokens_per_day.toLocaleString()}</strong></p>
              <p>Max cost/month: <strong>${quota.max_cost_per_month.toFixed(2)}</strong></p>
              <button className="btn btn-secondary btn-sm" onClick={() => setEditing(true)} style={{ marginTop: 8 }}>
                Edit Limits
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}