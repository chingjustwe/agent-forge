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
    <div>
      <div className="page-header">
        <h1 className="page-title">Admin Overview</h1>
        <p className="page-subtitle">Manage workspaces, users, and platform settings</p>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="stat-grid">
        <div className="stat-card stat-card-accent">
          <div className="stat-card-value">{workspaces.length}</div>
          <div className="stat-card-label">Workspaces</div>
        </div>
        <div className="stat-card stat-card-accent-success">
          <div className="stat-card-value">{users.length}</div>
          <div className="stat-card-label">Users</div>
        </div>
      </div>

      <div className="admin-nav">
        <a href="/admin/users" className="admin-nav-link">👥 Manage Users</a>
        <a href="/admin/workspaces" className="admin-nav-link">🏢 Manage Workspaces</a>
        <a href="/admin/audit" className="admin-nav-link">📝 Audit Log</a>
        <a href="/admin/usage" className="admin-nav-link">📈 Usage</a>
      </div>

      <section style={{ marginTop: 32 }}>
        <h2 className="detail-section-title">Workspaces</h2>
        <form onSubmit={handleCreateWorkspace} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          <input
            value={newWsName}
            onChange={(e) => setNewWsName(e.target.value)}
            placeholder="New workspace name"
            style={{ maxWidth: 300 }}
          />
          <button type="submit" className="btn btn-primary">Create</button>
        </form>
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Members</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {workspaces.map((ws) => (
                <tr key={ws.id}>
                  <td>{ws.name}</td>
                  <td>{ws.member_count ?? 0}</td>
                  <td style={{ color: "var(--text-secondary)", fontSize: "0.82rem" }}>
                    {new Date(ws.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section style={{ marginTop: 32 }}>
        <h2 className="detail-section-title">Users</h2>
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Role</th>
                <th>Workspaces</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.email}</td>
                  <td>{u.name}</td>
                  <td><span className="badge badge-primary">{u.role}</span></td>
                  <td>{u.workspace_count ?? (u.workspace_ids?.length ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}