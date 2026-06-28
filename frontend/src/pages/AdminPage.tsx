import { useState, useEffect } from "react";
import { listWorkspaces, createWorkspace, listAdminUsers, Workspace, User } from "../api";

export default function AdminPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [newWsName, setNewWsName] = useState("");
  const [error, setError] = useState("");

  async function loadData() {
    try {
      const [ws, us] = await Promise.all([listWorkspaces(), listAdminUsers()]);
      setWorkspaces(ws);
      setUsers(us);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  async function handleCreateWorkspace(e: React.FormEvent) {
    e.preventDefault();
    if (!newWsName.trim()) return;
    try {
      await createWorkspace(newWsName.trim());
      setNewWsName("");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create workspace");
    }
  }

  return (
    <div style={{ maxWidth: 960, margin: "16px auto", padding: 16 }}>
      <h2>Admin Panel</h2>
      {error && <p style={{ color: "red" }}>{error}</p>}

      <section style={{ marginBottom: 32 }}>
        <h3>Workspaces</h3>
        <form onSubmit={handleCreateWorkspace} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          <input
            value={newWsName}
            onChange={(e) => setNewWsName(e.target.value)}
            placeholder="New workspace name"
            style={{ flex: 1, padding: 8 }}
          />
          <button type="submit" style={{ padding: "8px 16px" }}>Create</button>
        </form>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              <th style={{ borderBottom: "1px solid #ccc", padding: 8 }}>Name</th>
              <th style={{ borderBottom: "1px solid #ccc", padding: 8 }}>Members</th>
              <th style={{ borderBottom: "1px solid #ccc", padding: 8 }}>Created</th>
            </tr>
          </thead>
          <tbody>
            {workspaces.map((ws) => (
              <tr key={ws.id}>
                <td style={{ padding: 8 }}>{ws.name}</td>
                <td style={{ padding: 8 }}>{ws.member_count ?? 0}</td>
                <td style={{ padding: 8 }}>{new Date(ws.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h3>Users</h3>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              <th style={{ borderBottom: "1px solid #ccc", padding: 8 }}>Email</th>
              <th style={{ borderBottom: "1px solid #ccc", padding: 8 }}>Name</th>
              <th style={{ borderBottom: "1px solid #ccc", padding: 8 }}>Role</th>
              <th style={{ borderBottom: "1px solid #ccc", padding: 8 }}>Workspaces</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td style={{ padding: 8 }}>{u.email}</td>
                <td style={{ padding: 8 }}>{u.name}</td>
                <td style={{ padding: 8 }}>{u.role}</td>
                <td style={{ padding: 8 }}>{u.workspace_count ?? (u.workspace_ids?.length ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
