import { useEffect, useState } from "react";
import { fetchUsers, updateUser, deleteUser, inviteUser, AdminUser } from "../api";

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

  const load = () => {
    setLoading(true);
    fetchUsers({ search: search || undefined, role: roleFilter || undefined })
      .then(setUsers)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

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
      await inviteUser({ email: inviteEmail, role: inviteRole });
      setShowInvite(false);
      setInviteEmail("");
      load();
    } catch (e: unknown) {
      setMessage(String(e));
    }
  };

  return (
    <div>
      <h1>User Management</h1>
      {message && <div style={{ color: "red", marginBottom: 12 }}>{message}</div>}

      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <input
          placeholder="Search email or name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={inputStyle}
        />
        <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} style={inputStyle}>
          <option value="">All roles</option>
          <option value="tenant_admin">Tenant Admin</option>
          <option value="workspace_owner">Workspace Owner</option>
          <option value="workspace_admin">Workspace Admin</option>
          <option value="member">Member</option>
          <option value="viewer">Viewer</option>
        </select>
        <button onClick={handleSearch} style={btnStyle}>Search</button>
        <button onClick={() => setShowInvite(true)} style={{ ...btnStyle, background: "#2d7d46" }}>
          Invite User
        </button>
      </div>

      {showInvite && (
        <div style={{ background: "#f5f5f5", padding: 16, borderRadius: 8, marginBottom: 16, display: "flex", gap: 8, alignItems: "center" }}>
          <input placeholder="Email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} style={inputStyle} />
          <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)} style={inputStyle}>
            <option value="member">Member</option>
            <option value="workspace_admin">Workspace Admin</option>
            <option value="workspace_owner">Workspace Owner</option>
            <option value="viewer">Viewer</option>
          </select>
          <button onClick={handleInvite} style={btnStyle}>Send Invite</button>
          <button onClick={() => setShowInvite(false)} style={btnStyle}>Cancel</button>
        </div>
      )}

      {loading ? (
        <div>Loading...</div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", background: "#eee" }}>
              <th style={thStyle}>Email</th>
              <th style={thStyle}>Name</th>
              <th style={thStyle}>Role</th>
              <th style={thStyle}>Workspaces</th>
              <th style={thStyle}>Created</th>
              <th style={thStyle}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td style={tdStyle}>{u.email}</td>
                <td style={tdStyle}>{u.name}</td>
                <td style={tdStyle}>
                  {editingId === u.id ? (
                    <select value={editRole} onChange={(e) => setEditRole(e.target.value)} style={inputStyle}>
                      <option value="tenant_admin">Tenant Admin</option>
                      <option value="workspace_owner">Workspace Owner</option>
                      <option value="workspace_admin">Workspace Admin</option>
                      <option value="member">Member</option>
                      <option value="viewer">Viewer</option>
                    </select>
                  ) : (
                    u.role
                  )}
                </td>
                <td style={tdStyle}>{Array.isArray(u.workspaces) ? u.workspaces.join(", ") : u.workspaces}</td>
                <td style={tdStyle}>{new Date(u.created_at).toLocaleDateString()}</td>
                <td style={tdStyle}>
                  {editingId === u.id ? (
                    <button onClick={() => handleSaveRole(u.id)} style={btnStyle}>Save</button>
                  ) : (
                    <button onClick={() => { setEditingId(u.id); setEditRole(u.role); }} style={btnStyle}>Edit</button>
                  )}
                  <button onClick={() => handleDelete(u.id)} style={{ ...btnStyle, background: "#c0392b", marginLeft: 4 }}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = { padding: "8px 12px", border: "1px solid #ccc", borderRadius: 4, fontSize: 14 };
const btnStyle: React.CSSProperties = { padding: "8px 16px", background: "#1a1a2e", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 14 };
const thStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "8px 12px", borderBottom: "1px solid #eee" };
