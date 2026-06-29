import { useEffect, useState } from "react";
import { fetchUsage, UsageData } from "../api";

export default function AdminUsage() {
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  const load = () => {
    setLoading(true);
    fetchUsage({ since: since || undefined, until: until || undefined })
      .then(setData)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Usage Dashboard</h1>
        <p className="page-subtitle">Platform-wide usage metrics and cost breakdown</p>
      </div>

      <div className="filter-bar">
        <label style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>From:</label>
        <input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
        <label style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>To:</label>
        <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        <button className="btn btn-secondary" onClick={load}>Apply</button>
      </div>

      {loading ? (
        <div className="loading">Loading usage data</div>
      ) : data ? (
        <>
          <div className="stat-grid">
            <div className="stat-card stat-card-accent">
              <div className="stat-card-value">{data.total_requests.toLocaleString()}</div>
              <div className="stat-card-label">Total Requests</div>
            </div>
            <div className="stat-card stat-card-accent-success">
              <div className="stat-card-value">{data.total_tokens.toLocaleString()}</div>
              <div className="stat-card-label">Total Tokens</div>
            </div>
            <div className="stat-card stat-card-accent-warning">
              <div className="stat-card-value">${data.total_cost.toFixed(2)}</div>
              <div className="stat-card-label">Total Cost</div>
            </div>
          </div>

          {data.by_workspace.length > 0 && (
            <div className="detail-section">
              <h2 className="detail-section-title">Per-Workspace Breakdown</h2>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Workspace</th>
                      <th>Requests</th>
                      <th>Tokens</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_workspace.map((ws) => (
                      <tr key={ws.workspace_id}>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{ws.workspace_id}</td>
                        <td>{ws.total_requests.toLocaleString()}</td>
                        <td>{ws.total_tokens.toLocaleString()}</td>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>${ws.total_cost.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--text-muted)" }}>
          No usage data available
        </div>
      )}
    </div>
  );
}