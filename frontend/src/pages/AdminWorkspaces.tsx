import { useEffect, useState, useRef } from "react";
import { fetchAdminWorkspaces, createAdminWorkspace, updateAdminWorkspace, archiveWorkspace, purgeWorkspace, setDefaultWorkspace, addWorkspaceMember, removeWorkspaceMember, fetchWorkspaceMembers, fetchUsers, AdminWorkspace, WorkspaceMember, AdminUser } from "../api";
import { Modal } from "../components/Modal";
import { Select } from "../components/Select";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { Dropdown } from "../components/Dropdown";
import { SkeletonTable } from "../components/Skeleton";

export default function AdminWorkspaces() {
  const [workspaces, setWorkspaces] = useState<AdminWorkspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [editWs, setEditWs] = useState<AdminWorkspace | null>(null);
  const [editName, setEditName] = useState("");
  const [editSlug, setEditSlug] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editIcon, setEditIcon] = useState("");
  const [editTokens, setEditTokens] = useState(0);
  const [editCost, setEditCost] = useState(0);
  const [saving, setSaving] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newWsName, setNewWsName] = useState("");
  const [newWsSlug, setNewWsSlug] = useState("");
  const [newWsDescription, setNewWsDescription] = useState("");
  const [newWsIcon, setNewWsIcon] = useState("");
  const [newWsTokens, setNewWsTokens] = useState(0);
  const [newWsCost, setNewWsCost] = useState(0);

  // Member management state
  const [memberWsId, setMemberWsId] = useState<string | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [memberLoading, setMemberLoading] = useState(false);
  const [addRole, setAddRole] = useState("member");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<AdminUser[]>([]);
  const [searching, setSearching] = useState(false);
  const [addingUser, setAddingUser] = useState<string | null>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showDropdown, setShowDropdown] = useState(false);

  // Archive/Purge confirmation
  const [archiveTarget, setArchiveTarget] = useState<AdminWorkspace | null>(null);
  const [removeMemberTarget, setRemoveMemberTarget] = useState<{ userId: string; email: string } | null>(null);
  const [removingMember, setRemovingMember] = useState(false);

  // Purge confirmation state
  const [purgeWs, setPurgeWs] = useState<AdminWorkspace | null>(null);
  const [purgeInput, setPurgeInput] = useState("");
  const [purging, setPurging] = useState(false);

  // Set-default tracking
  const [settingDefault, setSettingDefault] = useState<string | null>(null);

  const toast = useToast();

  const load = () => {
    setLoading(true);
    fetchAdminWorkspaces({ include_archived: true })
      .then(setWorkspaces)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  // ── Create workspace (Modal) ──

  const openCreate = () => {
    setNewWsName("");
    setNewWsSlug("");
    setNewWsDescription("");
    setNewWsIcon("");
    setNewWsTokens(0);
    setNewWsCost(0);
    setCreateOpen(true);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWsName.trim()) return;
    setCreating(true);
    try {
      await createAdminWorkspace(newWsName.trim(), {
        slug: newWsSlug.trim() || undefined,
        description: newWsDescription.trim() || undefined,
        icon: newWsIcon.trim() || undefined,
        max_tokens_per_day: newWsTokens || undefined,
        max_cost_per_month: newWsCost || undefined,
      });
      toast.success("Workspace created");
      setCreateOpen(false);
      load();
    } catch (e: unknown) {
      toast.error("Create failed", e instanceof Error ? e.message : "Failed to create workspace");
    } finally {
      setCreating(false);
    }
  };

  // ── Edit workspace (Modal) ──

  const openEdit = (ws: AdminWorkspace) => {
    setEditWs(ws);
    setEditName(ws.name);
    setEditSlug(ws.slug || "");
    setEditDescription(ws.description || "");
    setEditIcon(ws.icon || "");
    setEditTokens(0);
    setEditCost(0);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editWs) return;
    setSaving(true);
    try {
      await updateAdminWorkspace(editWs.id, {
        name: editName,
        slug: editSlug,
        description: editDescription,
        icon: editIcon,
        max_tokens_per_day: editTokens,
        max_cost_per_month: editCost,
      });
      toast.success("Workspace updated");
      setEditWs(null);
      load();
    } catch (e: unknown) {
      toast.error("Update failed", e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  // ── Archive ──

  const handleArchive = async () => {
    if (!archiveTarget) return;
    try {
      await archiveWorkspace(archiveTarget.id);
      toast.success("Workspace archived");
      setArchiveTarget(null);
      load();
    } catch (e: unknown) {
      toast.error("Archive failed", e instanceof Error ? e.message : String(e));
    }
  };

  // ── Purge (custom confirmation with name typing) ──

  const handlePurge = async () => {
    if (!purgeWs) return;
    if (purgeInput !== purgeWs.name) return;
    setPurging(true);
    try {
      await purgeWorkspace(purgeWs.id, purgeInput);
      toast.success("Workspace purged");
      setPurgeWs(null);
      setPurgeInput("");
      load();
    } catch (e: unknown) {
      toast.error("Purge failed", e instanceof Error ? e.message : "Failed to purge workspace");
    } finally {
      setPurging(false);
    }
  };

  // ── Set default ──

  const handleSetDefault = async (id: string) => {
    setSettingDefault(id);
    try {
      await setDefaultWorkspace(id);
      toast.success("Default workspace updated");
      load();
    } catch (e: unknown) {
      toast.error("Failed to set default", e instanceof Error ? e.message : "Failed to set default workspace");
    } finally {
      setSettingDefault(null);
    }
  };

  // ── Members ──

  const openMembers = async (wsId: string) => {
    setMemberWsId(wsId);
    setMemberLoading(true);
    setSearchQuery("");
    setSearchResults([]);
    setShowDropdown(false);
    setAddRole("member");
    try {
      const data = await fetchWorkspaceMembers(wsId);
      setMembers(data);
    } catch (e: unknown) {
      toast.error("Failed to load members", e instanceof Error ? e.message : String(e));
    } finally {
      setMemberLoading(false);
    }
  };

  const handleAddMember = async (userId: string, userName: string) => {
    if (!memberWsId) return;
    setAddingUser(userId);
    try {
      await addWorkspaceMember(memberWsId, userId, addRole);
      toast.success("Member added", `${userName} was added to the workspace`);
      setSearchQuery("");
      setSearchResults([]);
      setShowDropdown(false);
      await openMembers(memberWsId);
      load();
    } catch (e: unknown) {
      toast.error("Failed to add member", e instanceof Error ? e.message : String(e));
    } finally {
      setAddingUser(null);
    }
  };

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!value.trim()) {
      setSearchResults([]);
      setShowDropdown(false);
      return;
    }
    searchTimer.current = setTimeout(async () => {
      setSearching(true);
      try {
        const users = await fetchUsers({ search: value.trim() });
        const memberIds = new Set(members.map(m => m.user_id));
        setSearchResults(users.filter(u => !memberIds.has(u.id)));
        setShowDropdown(true);
      } catch {
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
  };

  const handleRemoveMember = async () => {
    if (!memberWsId || !removeMemberTarget) return;
    setRemovingMember(true);
    try {
      await removeWorkspaceMember(memberWsId, removeMemberTarget.userId);
      toast.success("Member removed");
      setRemoveMemberTarget(null);
      await openMembers(memberWsId);
      load();
    } catch (e: unknown) {
      toast.error("Failed to remove member", e instanceof Error ? e.message : String(e));
    } finally {
      setRemovingMember(false);
    }
  };

  const memberWsName = memberWsId ? (workspaces.find(w => w.id === memberWsId)?.name || memberWsId) : "";

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Workspace Management</h1>
        <p className="page-subtitle">Manage workspaces, quotas, and settings</p>
        <div style={{ marginLeft: "auto" }}>
          <button className="btn btn-primary" onClick={openCreate}>+ New Workspace</button>
        </div>
      </div>

      {loading ? (
        <SkeletonTable rows={5} cols={7} />
      ) : workspaces.length === 0 ? (
        <EmptyState
          title="No workspaces"
          description="Create your first workspace to get started."
          action={{ label: "+ New Workspace", onClick: openCreate }}
        />
      ) : (
        <>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 40 }}>Icon</th>
                  <th>Name</th>
                  <th>Slug</th>
                  <th>Description</th>
                  <th>Members</th>
                  <th>Agents</th>
                  <th>Created</th>
                  <th style={{ width: 60 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {workspaces.map((ws) => (
                  <tr key={ws.id}>
                    <td style={{ width: 32, maxWidth: 32, overflow: "hidden", textAlign: "center" }}>
                      {ws.icon ? (
                        /^https?:\/\//.test(ws.icon) ? (
                          <img src={ws.icon} alt="" style={{ width: 20, height: 20, objectFit: "contain", verticalAlign: "middle" }} />
                        ) : (
                          <span style={{ fontSize: "1.1rem" }}>{ws.icon}</span>
                        )
                      ) : null}
                    </td>
                    <td>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                        {ws.name}
                        {ws.is_default && (
                          <span className="badge badge-success" style={{ fontSize: "0.7rem" }}>Default</span>
                        )}
                        {ws.archived && (
                          <span className="badge badge-error" style={{ fontSize: "0.7rem" }}>Archived</span>
                        )}
                      </span>
                    </td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>{ws.slug || ""}</td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>{ws.description || ""}</td>
                    <td>{ws.member_count}</td>
                    <td>{ws.agent_count}</td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {new Date(ws.created_at).toLocaleDateString()}
                    </td>
                    <td>
                      {ws.archived ? (
                        <Dropdown
                          items={[
                            {
                              label: "Purge permanently",
                              variant: "danger",
                              onClick: () => { setPurgeWs(ws); setPurgeInput(""); },
                            },
                          ]}
                        />
                      ) : (
                        <Dropdown
                          items={[
                            { label: "Edit", onClick: () => openEdit(ws) },
                            { label: "Members", onClick: () => openMembers(ws.id) },
                            ...(!ws.is_default ? [{
                              label: settingDefault === ws.id ? "Setting..." : "Set as default",
                              onClick: () => handleSetDefault(ws.id),
                            }] : []),
                            ...(!ws.is_default ? [{
                              label: "Archive",
                              variant: "danger" as const,
                              onClick: () => setArchiveTarget(ws),
                            }] : []),
                          ]}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Member management modal */}
      <Modal
        open={!!memberWsId}
        onClose={() => setMemberWsId(null)}
        title={`Members of ${memberWsName}`}
        width="lg"
        footer={
          <button className="btn btn-secondary" onClick={() => setMemberWsId(null)}>Close</button>
        }
      >
        {memberLoading ? (
          <SkeletonTable rows={3} cols={4} />
        ) : (
          <>
            {members.length === 0 ? (
              <div style={{ padding: 16, textAlign: "center", color: "var(--text-muted)" }}>
                No members yet. Use the search below to add members.
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Email</th>
                    <th>Name</th>
                    <th>Role</th>
                    <th style={{ width: 80 }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((m) => (
                    <tr key={m.user_id}>
                      <td>{m.email}</td>
                      <td>{m.name}</td>
                      <td><span className="badge badge-primary">{m.role}</span></td>
                      <td>
                        <Dropdown
                          items={[
                            {
                              label: "Remove",
                              variant: "danger",
                              onClick: () => setRemoveMemberTarget({ userId: m.user_id, email: m.email }),
                            },
                          ]}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <div style={{ padding: "12px 0 0", borderTop: "1px solid var(--border)", marginTop: 12 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
                <div className="form-group" style={{ flex: 1, margin: 0, position: "relative" }}>
                  <label className="form-label">Search registered users</label>
                  <input
                    value={searchQuery}
                    onChange={e => handleSearch(e.target.value)}
                    placeholder="Search by name or email..."
                    onFocus={() => searchResults.length > 0 && setShowDropdown(true)}
                    onBlur={() => setTimeout(() => setShowDropdown(false), 200)}
                  />
                  {showDropdown && (
                    <div style={{
                      position: "absolute", top: "100%", left: 0, right: 0,
                      background: "var(--bg)", border: "1px solid var(--border)",
                      borderRadius: 6, maxHeight: 200, overflowY: "auto",
                      zIndex: 100, boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                    }}>
                      {searching ? (
                        <div style={{ padding: 8, color: "var(--text-muted)", fontSize: "0.85rem" }}>Searching...</div>
                      ) : searchResults.length === 0 ? (
                        <div style={{ padding: 8, color: "var(--text-muted)", fontSize: "0.85rem" }}>
                          {searchQuery ? "No matching users found" : "Start typing to search"}
                        </div>
                      ) : (
                        searchResults.map(u => (
                          <div key={u.id}
                            style={{
                              display: "flex", alignItems: "center", gap: 8,
                              padding: "6px 8px", cursor: "pointer",
                              borderBottom: "1px solid var(--border)",
                            }}
                            onMouseDown={() => handleAddMember(u.id, u.name || u.email)}
                          >
                            <div style={{ flex: 1 }}>
                              <div style={{ fontSize: "0.9rem" }}>{u.name || u.email}</div>
                              <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{u.email}</div>
                            </div>
                            <span className="badge badge-primary" style={{ fontSize: "0.7rem" }}>{u.role}</span>
                            <button
                              className="btn btn-primary btn-sm"
                              disabled={addingUser === u.id}
                            >
                              {addingUser === u.id ? "Adding..." : "Add"}
                            </button>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>
                <div className="form-group" style={{ margin: 0, width: 150, flexShrink: 0 }}>
                  <label className="form-label">Role</label>
                  <Select
                    value={addRole}
                    onChange={setAddRole}
                    options={[
                      { value: "member", label: "Member" },
                      { value: "workspace_admin", label: "Admin" },
                      { value: "viewer", label: "Viewer" },
                    ]}
                  />
                </div>
              </div>
            </div>
          </>
        )}
      </Modal>
        </>
      )}

      {/* Create Workspace Modal */}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create Workspace"
      >
        <form onSubmit={handleCreate}>
          <div className="form-group">
            <label className="form-label">Workspace name *</label>
            <input
              value={newWsName}
              onChange={(e) => setNewWsName(e.target.value)}
              placeholder="Workspace name"
              autoFocus
            />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Icon (emoji or URL)</label>
              <input
                value={newWsIcon}
                onChange={(e) => setNewWsIcon(e.target.value)}
                placeholder="Icon"
              />
            </div>
            <div className="form-group">
              <label className="form-label">Slug</label>
              <input
                value={newWsSlug}
                onChange={(e) => setNewWsSlug(e.target.value)}
                placeholder="Auto-generated from name"
              />
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Description</label>
            <input
              value={newWsDescription}
              onChange={(e) => setNewWsDescription(e.target.value)}
              placeholder="Description (optional)"
            />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Tokens per day</label>
              <input type="number" value={newWsTokens} onChange={(e) => setNewWsTokens(Number(e.target.value))} />
            </div>
            <div className="form-group">
              <label className="form-label">Cost per month ($)</label>
              <input type="number" value={newWsCost} onChange={(e) => setNewWsCost(Number(e.target.value))} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-secondary" onClick={() => setCreateOpen(false)}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={creating}>
              {creating ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </Modal>

      {/* Edit Workspace Modal */}
      <Modal
        open={!!editWs}
        onClose={() => setEditWs(null)}
        title="Edit Workspace"
      >
        <form onSubmit={handleSave}>
          <div className="form-group">
            <label className="form-label">Name *</label>
            <input value={editName} onChange={(e) => setEditName(e.target.value)} autoFocus />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Slug</label>
              <input value={editSlug} onChange={(e) => setEditSlug(e.target.value)} placeholder="slug" />
            </div>
            <div className="form-group">
              <label className="form-label">Icon</label>
              <input value={editIcon} onChange={(e) => setEditIcon(e.target.value)} placeholder="emoji / URL" />
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Description</label>
            <input value={editDescription} onChange={(e) => setEditDescription(e.target.value)} placeholder="description" />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Tokens per day</label>
              <input type="number" value={editTokens} onChange={(e) => setEditTokens(Number(e.target.value))} />
            </div>
            <div className="form-group">
              <label className="form-label">Cost per month ($)</label>
              <input type="number" value={editCost} onChange={(e) => setEditCost(Number(e.target.value))} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-secondary" onClick={() => setEditWs(null)}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </form>
      </Modal>

      {/* Archive ConfirmDialog */}
      <ConfirmDialog
        open={!!archiveTarget}
        onClose={() => setArchiveTarget(null)}
        onConfirm={handleArchive}
        title="Archive workspace"
        description={`Are you sure you want to archive "${archiveTarget?.name}"? Archived workspaces can be purged later.`}
        confirmText="Archive"
        variant="danger"
      />

      {/* Remove Member ConfirmDialog */}
      <ConfirmDialog
        open={!!removeMemberTarget}
        onClose={() => setRemoveMemberTarget(null)}
        onConfirm={handleRemoveMember}
        title="Remove member"
        description={`Remove ${removeMemberTarget?.email} from this workspace?`}
        confirmText="Remove"
        variant="danger"
        loading={removingMember}
      />

      {/* Purge workspace modal (custom: requires name typing) */}
      <Modal
        open={!!purgeWs}
        onClose={() => !purging && setPurgeWs(null)}
        title="Purge workspace"
        width="sm"
        closeOnBackdrop={!purging}
        closeOnEsc={!purging}
      >
        <div className="alert alert-error">
          This will permanently delete <strong>{purgeWs?.name}</strong> and ALL its data
          (sessions, agents, API keys, invitations, logs). This cannot be undone.
        </div>
        <div className="form-group" style={{ marginTop: 12 }}>
          <label className="form-label">
            Type the workspace name to confirm:
          </label>
          <input
            type="text"
            value={purgeInput}
            onChange={e => setPurgeInput(e.target.value)}
            placeholder={purgeWs?.name || ""}
            autoFocus
            style={{ fontFamily: "monospace" }}
          />
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
          <button
            className="btn btn-danger"
            onClick={handlePurge}
            disabled={purging || purgeInput !== purgeWs?.name}
          >
            {purging ? "Purging..." : "Purge permanently"}
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => setPurgeWs(null)}
            disabled={purging}
          >
            Cancel
          </button>
        </div>
      </Modal>
    </div>
  );
}
