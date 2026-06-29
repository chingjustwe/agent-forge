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
      <div className="page-header">
        <h1 className="page-title">Workspace Management</h1>
        <p className="page-subtitle">Manage workspaces, quotas, and settings</p>
      </div>

      {message && <div className="alert alert-error">{message}</div>}

      {loading ? (
        <div className="loading">Loading workspaces</div>
      ) : (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Members</th>
                <th>Agents</th>
                <th>Owner</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {workspaces.map((ws) => (
                <tr key={ws.id}>
                  <td>
                    {editingId === ws.id ? (
                      <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                    ) : (
                      ws.name
                    )}
                  </td>
                  <td>{ws.member_count}</td>
                  <td>{ws.agent_count}</td>
                  <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>{ws.owner}</td>
                  <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                    {new Date(ws.created_at).toLocaleDateString()}
                  </td>
                  <td>
                    {editingId === ws.id ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <div>
                          <label style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginRight: 4 }}>Tokens/day:</label>
                          <input type="number" value={editTokens} onChange={(e) => setEditTokens(Number(e.target.value))} style={{ width: 80 }} />
                        </div>
                        <div>
                          <label style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginRight: 4 }}>Cost/month:</label>
                          <input type="number" value={editCost} onChange={(e) => setEditCost(Number(e.target.value))} style={{ width: 80 }} />
                        </div>
                        <div className="btn-group">
                          <button className="btn btn-primary btn-sm" onClick={() => handleSave(ws.id)}>Save</button>
                          <button className="btn btn-secondary btn-sm" onClick={() => setEditingId(null)}>Cancel</button>
                        </div>
                      </div>
                    ) : (
                      <div className="btn-group">
                        <button className="btn btn-secondary btn-sm" onClick={() => { setEditingId(ws.id); setEditName(ws.name); setEditTokens(0); setEditCost(0); }}>Edit</button>
                        <button className="btn btn-danger btn-sm" onClick={() => handleArchive(ws.id)}>Archive</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}