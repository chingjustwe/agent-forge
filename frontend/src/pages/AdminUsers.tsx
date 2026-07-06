import { useEffect, useState } from "react";
import {
  fetchUsers,
  updateUser,
  deleteUser,
  inviteUser,
  fetchAdminWorkspaces,
  listPendingInvitations,
  deletePendingInvitation,
  AdminUser,
  AdminWorkspace,
  PendingInvitation,
} from "../api";
import { Modal } from "../components/Modal";
import { Select } from "../components/Select";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { Dropdown } from "../components/Dropdown";
import { SkeletonTable } from "../components/Skeleton";

function roleLabel(role: string): string {
  switch (role) {
    case "tenant_admin": return "Tenant Admin";
    case "workspace_admin": return "Workspace Admin";
    case "member": return "Member";
    case "viewer": return "Viewer";
    default: return role;
  }
}

function countdown(expiresAt: string): string {
  const diff = new Date(expiresAt).getTime() - Date.now();
  if (diff <= 0) return "Expired";
  const days = Math.floor(diff / 86400000);
  const hours = Math.floor((diff % 86400000) / 3600000);
  if (days > 0) return `${days}d ${hours}h`;
  return `${hours}h ${Math.floor((diff % 3600000) / 60000)}m`;
}

export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [pending, setPending] = useState<PendingInvitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [workspaces, setWorkspaces] = useState<AdminWorkspace[]>([]);

  // Invite modal state
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteWsId, setInviteWsId] = useState<string>("");
  const [inviteExpires, setInviteExpires] = useState(7);
  const [inviting, setInviting] = useState(false);

  // Delete confirmations
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [cancelInviteTarget, setCancelInviteTarget] = useState<PendingInvitation | null>(null);
  const [cancellingInvite, setCancellingInvite] = useState(false);

  // Role editing
  const [roleEditUser, setRoleEditUser] = useState<AdminUser | null>(null);
  const [editRole, setEditRole] = useState("");
  const [savingRole, setSavingRole] = useState(false);

  const toast = useToast();

  const load = () => {
    setLoading(true);
    Promise.all([
      fetchUsers({ search: search || undefined, role: roleFilter || undefined }),
      listPendingInvitations().catch(() => [] as PendingInvitation[]),
    ])
      .then(([u, p]) => { setUsers(u); setPending(p); })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    fetchAdminWorkspaces().then(setWorkspaces).catch(() => {});
  }, []);

  const handleSearch = () => load();

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteUser(deleteTarget.id);
      toast.success("User deleted");
      setDeleteTarget(null);
      load();
    } catch (e: unknown) {
      toast.error("Delete failed", e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  };

  const handleDeletePending = async () => {
    if (!cancelInviteTarget) return;
    setCancellingInvite(true);
    try {
      await deletePendingInvitation(cancelInviteTarget.user_id);
      toast.success("Invitation cancelled", `Invitation for ${cancelInviteTarget.email} has been cancelled`);
      setCancelInviteTarget(null);
      load();
    } catch (e: unknown) {
      toast.error("Cancel failed", e instanceof Error ? e.message : String(e));
    } finally {
      setCancellingInvite(false);
    }
  };

  const handleSaveRole = async () => {
    if (!roleEditUser) return;
    setSavingRole(true);
    try {
      await updateUser(roleEditUser.id, { role: editRole });
      toast.success("Role updated", `${roleEditUser.email} is now ${roleLabel(editRole)}`);
      setRoleEditUser(null);
      load();
    } catch (e: unknown) {
      toast.error("Update failed", e instanceof Error ? e.message : String(e));
    } finally {
      setSavingRole(false);
    }
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    setInviting(true);
    try {
      const result = await inviteUser({ email: inviteEmail, role: inviteRole, workspace_id: inviteWsId || undefined, expires_in_days: inviteExpires });
      if (result.email_error) {
        toast.warning("Invitation created but email not sent", `Invite record created for ${inviteEmail}, but email delivery failed: ${result.email_error}`);
      } else {
        toast.success("Invitation sent", `Invitation sent to ${inviteEmail}`);
      }
      setInviteOpen(false);
      setInviteEmail("");
      setInviteWsId("");
      setInviteExpires(7);
      load();
    } catch (e: unknown) {
      toast.error("Invite failed", e instanceof Error ? e.message : String(e));
    } finally {
      setInviting(false);
    }
  };

  const openInvite = () => {
    setInviteEmail("");
    setInviteRole("member");
    setInviteWsId("");
    setInviteExpires(7);
    setInviteOpen(true);
  };

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">User Management</h1>
        <p className="page-subtitle">Manage users, roles, and invitations</p>
      </div>

      <div className="filter-bar">
        <input
          placeholder="Search email or name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Select
          value={roleFilter}
          onChange={setRoleFilter}
          options={[
            { value: "", label: "All roles" },
            { value: "tenant_admin", label: "Tenant Admin" },
            { value: "member", label: "Member" },
          ]}
        />
        <button className="btn btn-secondary" onClick={handleSearch}>Search</button>
        <div style={{ marginLeft: "auto" }}>
          <button className="btn btn-primary" onClick={openInvite}>Invite User</button>
        </div>
      </div>

      {loading ? (
        <SkeletonTable rows={5} cols={5} />
      ) : (
        <>
          <h2 style={{ fontSize: "1rem", marginBottom: 8, color: "var(--text-secondary)" }}>
            Active Users ({users.length})
          </h2>
          {users.length === 0 ? (
            <EmptyState
              title="No users found"
              description={search || roleFilter ? "Try adjusting your search or filters." : "No active users in the system."}
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
                    <th>Created</th>
                    <th style={{ width: 60 }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>{u.email}</td>
                      <td>{u.name}</td>
                      <td>
                        <span
                          className="badge badge-primary"
                          style={{ cursor: "pointer" }}
                          onClick={() => { setRoleEditUser(u); setEditRole(u.role); }}
                          title="Click to change role"
                        >
                          {roleLabel(u.role)}
                        </span>
                      </td>
                      <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                        {Array.isArray(u.workspaces) ? u.workspaces.join(", ") : u.workspaces}
                      </td>
                      <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                        {new Date(u.created_at).toLocaleDateString()}
                      </td>
                      <td>
                        <Dropdown
                          items={[
                            { label: "Change role", onClick: () => { setRoleEditUser(u); setEditRole(u.role); } },
                            { label: "Delete", variant: "danger", onClick: () => setDeleteTarget(u) },
                          ]}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {pending.length > 0 && (
            <>
              <h2 style={{ fontSize: "1rem", marginTop: 28, marginBottom: 8, color: "var(--text-secondary)" }}>
                Pending Invitations ({pending.length})
              </h2>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Email</th>
                      <th>Role</th>
                      <th>Workspace</th>
                      <th>Invited</th>
                      <th>Expires</th>
                      <th style={{ width: 60 }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pending.map((p) => (
                      <tr key={p.user_id}>
                        <td>{p.email}</td>
                        <td><span className="badge badge-secondary">{roleLabel(p.invited_role || p.role)}</span></td>
                        <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                          {p.workspace_name || "\u2014"}
                        </td>
                        <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                          {p.invited_at ? new Date(p.invited_at).toLocaleDateString() : "\u2014"}
                        </td>
                        <td>
                          <span style={{
                            fontSize: "0.82rem",
                            color: new Date(p.expires_at).getTime() < Date.now() ? "var(--danger)" : "var(--text-secondary)"
                          }}>
                            {p.expires_at ? countdown(p.expires_at) : "\u2014"}
                          </span>
                        </td>
                        <td>
                          <Dropdown
                            items={[
                              {
                                label: "Cancel invitation",
                                variant: "danger",
                                onClick: () => setCancelInviteTarget(p),
                              },
                            ]}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}

      {/* Invite User Modal */}
      <Modal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        title="Invite User"
      >
        <form onSubmit={handleInvite}>
          <div className="form-group">
            <label className="form-label">Email *</label>
            <input placeholder="user@example.com" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} autoFocus />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Role</label>
              <Select
                value={inviteRole}
                onChange={setInviteRole}
                options={[
                  { value: "member", label: "Member" },
                  { value: "workspace_admin", label: "Workspace Admin" },
                  { value: "tenant_admin", label: "Tenant Admin" },
                ]}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Workspace</label>
              <Select
                value={inviteWsId}
                onChange={setInviteWsId}
                options={[
                  { value: "", label: "(Default)" },
                  ...workspaces.map((ws) => ({ value: ws.id, label: ws.name })),
                ]}
              />
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Expires</label>
            <Select
              value={String(inviteExpires)}
              onChange={(v) => setInviteExpires(Number(v))}
              options={[
                { value: "1", label: "1 day" },
                { value: "3", label: "3 days" },
                { value: "7", label: "7 days" },
                { value: "14", label: "14 days" },
                { value: "30", label: "30 days" },
              ]}
            />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-secondary" onClick={() => setInviteOpen(false)}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={inviting}>
              {inviting ? "Sending..." : "Send Invite"}
            </button>
          </div>
        </form>
      </Modal>

      {/* Edit Role Modal */}
      <Modal
        open={!!roleEditUser}
        onClose={() => setRoleEditUser(null)}
        title="Change Role"
        width="sm"
      >
        <p style={{ marginBottom: 12, fontSize: "0.9rem", color: "var(--text-secondary)" }}>
          Change role for <strong>{roleEditUser?.email}</strong>
        </p>
        <div className="form-group">
          <label className="form-label">Role</label>
          <Select
            value={editRole}
            onChange={setEditRole}
            options={[
              { value: "member", label: "Member" },
              { value: "workspace_admin", label: "Workspace Admin" },
              { value: "tenant_admin", label: "Tenant Admin" },
              { value: "viewer", label: "Viewer" },
            ]}
          />
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn btn-secondary" onClick={() => setRoleEditUser(null)}>Cancel</button>
          <button className="btn btn-primary" onClick={handleSaveRole} disabled={savingRole}>
            {savingRole ? "Saving..." : "Save"}
          </button>
        </div>
      </Modal>

      {/* Delete User ConfirmDialog */}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="Delete user"
        description={`Are you sure you want to delete "${deleteTarget?.email}"? This action cannot be undone.`}
        confirmText="Delete"
        variant="danger"
        loading={deleting}
      />

      {/* Cancel Invitation ConfirmDialog */}
      <ConfirmDialog
        open={!!cancelInviteTarget}
        onClose={() => setCancelInviteTarget(null)}
        onConfirm={handleDeletePending}
        title="Cancel invitation"
        description={`Cancel the pending invitation for "${cancelInviteTarget?.email}"?`}
        confirmText="Cancel Invitation"
        variant="danger"
        loading={cancellingInvite}
      />
    </div>
  );
}
