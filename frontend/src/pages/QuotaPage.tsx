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

  if (!quota) return <div style={{ padding: 24 }}>Loading...</div>;

  const pct = quota.max_tokens_per_day > 0
    ? Math.min(100, (quota.tokens_used / quota.max_tokens_per_day) * 100)
    : 0;

  return (
    <div style={{ padding: 24 }}>
      <h1>Quota Management</h1>

      <div style={{ background: "#fff", padding: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)", marginBottom: 16 }}>
        <h3>Today's Usage</h3>
        <div style={{ background: "#eee", borderRadius: 4, height: 24, width: "100%", marginBottom: 8 }}>
          <div style={{ background: "#0088FE", borderRadius: 4, height: 24, width: `${pct}%`, transition: "width 0.3s" }} />
        </div>
        <p>{quota.tokens_used} / {quota.max_tokens_per_day === 0 ? "Unlimited" : quota.max_tokens_per_day} tokens</p>
        <p>Cost today: ${quota.cost_today.toFixed(4)}</p>
      </div>

      {isAdmin && (
        <div style={{ background: "#fff", padding: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)" }}>
          <h3>Configuration</h3>
          {editing ? (
            <div>
              <label>Max Tokens Per Day: </label>
              <input type="number" value={editTokens} onChange={e => setEditTokens(Number(e.target.value))} style={{ marginRight: 8 }} />
              <button onClick={handleSave}>Save</button>
              <button onClick={() => setEditing(false)} style={{ marginLeft: 8 }}>Cancel</button>
            </div>
          ) : (
            <div>
              <p>Max tokens/day: {quota.max_tokens_per_day}</p>
              <p>Max cost/month: ${quota.max_cost_per_month}</p>
              <button onClick={() => setEditing(true)}>Edit</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
