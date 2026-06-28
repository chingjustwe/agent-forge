import { useEffect, useState } from "react";
import { fetchAdminWorkspaces, updateAdminWorkspace, archiveWorkspace, AdminWorkspace } from "../api";

export default function AdminWorkspaces() {
  const [workspaces, setWorkspaces] = useState<AdminWorkspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editTokens, setEditTokens] = useState(0);
  const [editCost, setEditCost] = useState(0);
  const [message, setMessage] = useState("");

  const load = () => {
    setLoading(true);
    fetchAdminWorkspaces()
      .then(setWorkspaces)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleSave = async (id: string) => {
    try {
      await updateAdminWorkspace(id, { name: editName, max_tokens_per_day: editTokens, max_cost_per_month: editCost });
      setEditingId(null);
      load();
    } catch (e: unknown) {
      setMessage(String(e));
    }
  };

  const handleArchive = async (id: string) => {
    if (!confirm("Archive this workspace?")) return;
    try {
      await archiveWorkspace(id);
      load();
    } catch (e: unknown) {
      setMessage(String(e));
    }
  };

  return (
    <div>
      <h1>Workspace Management</h1>
      {message && <div style={{ color: "red", marginBottom: 12 }}>{message}</div>}

      {loading ? (
        <div>Loading...</div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", background: "#eee" }}>
              <th style={thStyle}>Name</th>
              <th style={thStyle}>Members</th>
              <th style={thStyle}>Agents</th>
              <th style={thStyle}>Owner</th>
              <th style={thStyle}>Created</th>
              <th style={thStyle}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {workspaces.map((ws) => (
              <tr key={ws.id}>
                <td style={tdStyle}>
                  {editingId === ws.id ? (
                    <input value={editName} onChange={(e) => setEditName(e.target.value)} style={inputStyle} />
                  ) : (
                    ws.name
                  )}
                </td>
                <td style={tdStyle}>{ws.member_count}</td>
                <td style={tdStyle}>{ws.agent_count}</td>
                <td style={tdStyle}>{ws.owner}</td>
                <td style={tdStyle}>{new Date(ws.created_at).toLocaleDateString()}</td>
                <td style={tdStyle}>
                  {editingId === ws.id ? (
                    <>
                      <div style={{ marginBottom: 4 }}>
                        <label>Tokens/day: </label>
                        <input type="number" value={editTokens} onChange={(e) => setEditTokens(Number(e.target.value))} style={{ ...inputStyle, width: 80 }} />
                      </div>
                      <div style={{ marginBottom: 4 }}>
                        <label>Cost/month: </label>
                        <input type="number" value={editCost} onChange={(e) => setEditCost(Number(e.target.value))} style={{ ...inputStyle, width: 80 }} />
                      </div>
                      <button onClick={() => handleSave(ws.id)} style={btnStyle}>Save</button>
                      <button onClick={() => setEditingId(null)} style={{ ...btnStyle, marginLeft: 4 }}>Cancel</button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => { setEditingId(ws.id); setEditName(ws.name); }} style={btnStyle}>Edit</button>
                      <button onClick={() => handleArchive(ws.id)} style={{ ...btnStyle, background: "#c0392b", marginLeft: 4 }}>Archive</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = { padding: "6px 10px", border: "1px solid #ccc", borderRadius: 4, fontSize: 14 };
const btnStyle: React.CSSProperties = { padding: "6px 14px", background: "#1a1a2e", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 13 };
const thStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "1px solid #eee" };
