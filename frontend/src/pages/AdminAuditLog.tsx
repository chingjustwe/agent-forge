import { useEffect, useState } from "react";
import { fetchAdminAudit, AuditEntry, AuditResponse } from "../api";

const ACTIONS = [
  "",
  "workspace.create",
  "workspace.update",
  "workspace.delete",
  "user.role_change",
  "user.invite",
  "user.delete",
];

export default function AdminAuditLog() {
  const [data, setData] = useState<AuditResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState("");
  const [userId, setUserId] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const limit = 20;

  const load = () => {
    setLoading(true);
    fetchAdminAudit({
      action: action || undefined,
      user_id: userId || undefined,
      since: since || undefined,
      until: until || undefined,
      limit,
      offset,
    })
      .then(setData)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [offset]);

  const totalPages = data ? Math.ceil(data.total / limit) : 0;
  const currentPage = Math.floor(offset / limit) + 1;

  return (
    <div>
      <h1>Audit Log</h1>

      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap", alignItems: "center" }}>
        <select value={action} onChange={(e) => setAction(e.target.value)} style={inputStyle}>
          {ACTIONS.map((a) => (
            <option key={a} value={a}>{a || "All actions"}</option>
          ))}
        </select>
        <input placeholder="User ID" value={userId} onChange={(e) => setUserId(e.target.value)} style={inputStyle} />
        <input type="date" value={since} onChange={(e) => setSince(e.target.value)} style={inputStyle} />
        <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} style={inputStyle} />
        <button onClick={load} style={btnStyle}>Filter</button>
      </div>

      {loading ? (
        <div>Loading...</div>
      ) : (
        <>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", background: "#eee" }}>
                <th style={thStyle}>Timestamp</th>
                <th style={thStyle}>User</th>
                <th style={thStyle}>Action</th>
                <th style={thStyle}>Target</th>
                <th style={thStyle}>IP</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((entry: AuditEntry) => (
                <>
                  <tr
                    key={entry.id}
                    onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                    style={{ cursor: "pointer" }}
                  >
                    <td style={tdStyle}>{new Date(entry.created_at).toLocaleString()}</td>
                    <td style={tdStyle}>{entry.user_id}</td>
                    <td style={tdStyle}>{entry.action}</td>
                    <td style={tdStyle}>{entry.target_type}:{entry.target_id}</td>
                    <td style={tdStyle}>{entry.ip_address}</td>
                  </tr>
                  {expandedId === entry.id && (
                    <tr>
                      <td colSpan={5} style={{ padding: "12px 16px", background: "#f9f9f9" }}>
                        <strong>Details:</strong>
                        <pre style={{ background: "#eee", padding: 8, borderRadius: 4, fontSize: 12, maxHeight: 200, overflow: "auto" }}>
                          {JSON.stringify(entry.details, null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>

          {data && (
            <div style={{ marginTop: 16, display: "flex", gap: 8, alignItems: "center" }}>
              <button disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - limit))} style={btnStyle}>Previous</button>
              <span>Page {currentPage} of {totalPages || 1} ({data.total} total)</span>
              <button disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)} style={btnStyle}>Next</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = { padding: "8px 12px", border: "1px solid #ccc", borderRadius: 4, fontSize: 14 };
const btnStyle: React.CSSProperties = { padding: "8px 16px", background: "#1a1a2e", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 14 };
const thStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "1px solid #eee" };
