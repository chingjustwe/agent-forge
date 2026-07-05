import { useState, useEffect } from "react";
import { listWorkspaces, createWorkspace, listAdminUsers, Workspace, User } from "../api";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { EmptyState } from "../components/EmptyState";
import { SkeletonTable } from "../components/Skeleton";

export default function AdminPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Create workspace modal
  const [createOpen, setCreateOpen] = useState(false);
  const [newWsName, setNewWsName] = useState("");
  const [creating, setCreating] = useState(false);

  const toast = useToast();

  async function loadData() {
    setLoading(true);
    try {
      const [ws, us] = await Promise.all([listWorkspaces(), listAdminUsers()]);
      setWorkspaces(ws);
      setUsers(us);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
      toast.error("Load failed", err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  async function handleCreateWorkspace(e: React.FormEvent) {
    e.preventDefault();
    if (!newWsName.trim()) return;
    setCreating(true);
    try {
      await createWorkspace(newWsName.trim());
      toast.success("Workspace created");
      setCreateOpen(false);
      setNewWsName("");
      await loadData();
    } catch (err: unknown) {
      toast.error("Create failed", err instanceof Error ? err.message : "Failed to create workspace");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Admin Overview</h1>
        <p className="page-subtitle">Manage workspaces, users, and platform settings</p>
      </div>

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

      <section style={{ marginTop: 32 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h2 className="detail-section-title" style={{ margin: 0 }}>Workspaces</h2>
          <button className="btn btn-primary btn-sm" onClick={() => { setNewWsName(""); setCreateOpen(true); }}>+ Create Workspace</button>
        </div>
        {loading ? (
          <SkeletonTable rows={5} cols={3} />
        ) : workspaces.length === 0 ? (
          <EmptyState
            title="No workspaces"
            description="Create your first workspace to get started."
            action={{ label: "+ Create Workspace", onClick: () => { setNewWsName(""); setCreateOpen(true); } }}
          />
        ) : (
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
        )}
      </section>

      <section style={{ marginTop: 32 }}>
        <h2 className="detail-section-title" style={{ marginBottom: 12 }}>Users</h2>
        {loading ? (
          <SkeletonTable rows={5} cols={4} />
        ) : users.length === 0 ? (
          <EmptyState
            title="No users"
            description="No users have been registered yet."
          />
        ) : (
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
        )}
      </section>

      {/* Create Workspace Modal */}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create Workspace"
        width="sm"
      >
        <form onSubmit={handleCreateWorkspace} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="form-group">
            <label className="form-label">Workspace name *</label>
            <input
              value={newWsName}
              onChange={(e) => setNewWsName(e.target.value)}
              placeholder="New workspace name"
              autoFocus
            />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-secondary" onClick={() => setCreateOpen(false)}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={creating}>
              {creating ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
