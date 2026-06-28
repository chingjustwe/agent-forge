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

  const cardStyle: React.CSSProperties = {
    background: "#f5f5f5",
    borderRadius: 8,
    padding: "20px 24px",
    flex: 1,
    minWidth: 150,
  };

  return (
    <div>
      <h1>Usage Dashboard</h1>

      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <label>From:</label>
        <input type="date" value={since} onChange={(e) => setSince(e.target.value)} style={inputStyle} />
        <label>To:</label>
        <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} style={inputStyle} />
        <button onClick={load} style={btnStyle}>Apply</button>
      </div>

      {loading ? (
        <div>Loading...</div>
      ) : data ? (
        <>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 32 }}>
            <div style={cardStyle}>
              <div style={{ fontSize: 28, fontWeight: "bold" }}>{data.total_requests.toLocaleString()}</div>
              <div style={{ color: "#666" }}>Total Requests</div>
            </div>
            <div style={cardStyle}>
              <div style={{ fontSize: 28, fontWeight: "bold" }}>{data.total_tokens.toLocaleString()}</div>
              <div style={{ color: "#666" }}>Total Tokens</div>
            </div>
            <div style={cardStyle}>
              <div style={{ fontSize: 28, fontWeight: "bold" }}>${data.total_cost.toFixed(2)}</div>
              <div style={{ color: "#666" }}>Total Cost</div>
            </div>
          </div>

          {data.by_workspace.length > 0 && (
            <div>
              <h2>Per-Workspace Breakdown</h2>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", background: "#eee" }}>
                    <th style={thStyle}>Workspace</th>
                    <th style={thStyle}>Requests</th>
                    <th style={thStyle}>Tokens</th>
                    <th style={thStyle}>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_workspace.map((ws) => (
                    <tr key={ws.workspace_id}>
                      <td style={tdStyle}>{ws.workspace_id}</td>
                      <td style={tdStyle}>{ws.total_requests.toLocaleString()}</td>
                      <td style={tdStyle}>{ws.total_tokens.toLocaleString()}</td>
                      <td style={tdStyle}>${ws.total_cost.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : (
        <div>No usage data available</div>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = { padding: "8px 12px", border: "1px solid #ccc", borderRadius: 4, fontSize: 14 };
const btnStyle: React.CSSProperties = { padding: "8px 16px", background: "#1a1a2e", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 14 };
const thStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "1px solid #eee" };
