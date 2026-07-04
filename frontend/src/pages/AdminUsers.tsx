import { useEffect, useState } from "react";
import { fetchUsers, updateUser, deleteUser, inviteUser, fetchAdminWorkspaces, AdminUser, AdminWorkspace } from "../api";

export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editRole, setEditRole] = useState("");
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [message, setMessage] = useState("");
  const [workspaces, setWorkspaces] = useState<AdminWorkspace[]>([]);
  const [inviteWsId, setInviteWsId] = useState<string>("");

  const load = () => {
    setLoading(true);
    fetchUsers({ search: search || undefined, role: roleFilter || undefined })
      .then(setUsers)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    fetchAdminWorkspaces().then(setWorkspaces).catch(() => {});
  }, []);

  const handleSearch = () => load();

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this user?")) return;
    try {
      await deleteUser(id);
      setUsers(users.filter((u) => u.id !== id));
    } catch (e: unknown) {
      setMessage(String(e));
    }
  };

  const handleSaveRole = async (id: string) => {
    try {
      await updateUser(id, { role: editRole });
      setEditingId(null);
      load();
    } catch (e: unknown) {
      setMessage(String(e));
    }
  };

  const handleInvite = async () => {
    try {
      await inviteUser({ email: inviteEmail, role: inviteRole, workspace_id: inviteWsId || undefined });
      setShowInvite(false);
      setInviteEmail("");
      setInviteWsId("");
      setMessage(`Invitation sent to ${inviteEmail}`);
      load();
    } catch (e: unknown) {
      setMessage(String(e));
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">User Management</h1>
        <p className="page-subtitle">Manage users, roles, and invitations</p>
      </div>

      {message && <div className="alert alert-error">{message}</div>}

      <div className="filter-bar">
        <input
          placeholder="Search email or name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ minWidth: 200 }}
        />
        <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
          <option value="">All roles</option>
          <option value="tenant_admin">Tenant Admin</option>
          <option value="workspace_owner">Workspace Owner</option>
          <option value="workspace_admin">Workspace Admin</option>
          <option value="member">Member</option>
          <option value="viewer">Viewer</option>
        </select>
        <button className="btn btn-secondary" onClick={handleSearch}>Search</button>
        <button className="btn btn-success" onClick={() => setShowInvite(true)}>
          Invite User
        </button>
      </div>

      {showInvite && (
        <div className="card" style={{ marginBottom: 20, display: "flex", gap: 10, alignItems: "flex-end" }}>
          <div className="form-group" style={{ margin: 0, flex: 1 }}>
            <label className="form-label">Email</label>
            <input placeholder="Email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} />
          </div>
          <div className="form-group" style={{ margin: 0 }}>
            <label className="form-label">Role</label>
            <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
              <option value="member">Member</option>
              <option value="tenant_admin">Tenant Admin</option>
            </select>
          </div>
          <div className="form-group" style={{ margin: 0 }}>
            <label className="form-label">Workspace</label>
            <select value={inviteWsId} onChange={(e) => setInviteWsId(e.target.value)}>
              <option value="">(Default)</option>
              {workspaces.map((ws) => (
                <option key={ws.id} value={ws.id}>{ws.name}</option>
              ))}
            </select>
          </div>
          <div className="btn-group">
            <button className="btn btn-primary" onClick={handleInvite}>Send Invite</button>
            <button className="btn btn-secondary" onClick={() => setShowInvite(false)}>Cancel</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="loading">Loading users</div>
      ) : (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Role</th>
                <th>Workspaces</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.email}</td>
                  <td>{u.name}</td>
                  <td>
                    {editingId === u.id ? (
                      <select value={editRole} onChange={(e) => setEditRole(e.target.value)}>
                        <option value="tenant_admin">Tenant Admin</option>
                        <option value="member">Member</option>
                      </select>
                    ) : (
                      <span className="badge badge-primary">{u.role}</span>
                    )}
                  </td>
                  <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                    {Array.isArray(u.workspaces) ? u.workspaces.join(", ") : u.workspaces}
                  </td>
                  <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td>
                    <div className="btn-group">
                      {editingId === u.id ? (
                        <button className="btn btn-primary btn-sm" onClick={() => handleSaveRole(u.id)}>Save</button>
                      ) : (
                        <button className="btn btn-secondary btn-sm" onClick={() => { setEditingId(u.id); setEditRole(u.role); }}>Edit</button>
                      )}
                      <button className="btn btn-danger btn-sm" onClick={() => handleDelete(u.id)}>Delete</button>
                    </div>
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