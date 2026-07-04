import { useEffect, useState, useRef } from "react";
import { fetchAdminWorkspaces, createAdminWorkspace, updateAdminWorkspace, archiveWorkspace, purgeWorkspace, setDefaultWorkspace, addWorkspaceMember, removeWorkspaceMember, fetchWorkspaceMembers, fetchUsers, AdminWorkspace, WorkspaceMember, AdminUser } from "../api";

export default function AdminWorkspaces() {
  const [workspaces, setWorkspaces] = useState<AdminWorkspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editSlug, setEditSlug] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editIcon, setEditIcon] = useState("");
  const [editTokens, setEditTokens] = useState(0);
  const [editCost, setEditCost] = useState(0);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState<"error" | "success">("error");
  const [creating, setCreating] = useState(false);
  const [newWsName, setNewWsName] = useState("");
  const [newWsSlug, setNewWsSlug] = useState("");
  const [newWsDescription, setNewWsDescription] = useState("");
  const [newWsIcon, setNewWsIcon] = useState("");

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

  // P3-3: purge confirmation state. `purgeWs` holds the workspace pending
  // confirmation; `purgeInput` is the typed-name field.
  const [purgeWs, setPurgeWs] = useState<AdminWorkspace | null>(null);
  const [purgeInput, setPurgeInput] = useState("");
  const [purging, setPurging] = useState(false);

  // P3-4: track in-flight set-default to disable the clicked button.
  const [settingDefault, setSettingDefault] = useState<string | null>(null);

  const showMessage = (msg: string, type: "error" | "success" = "error") => {
    setMessage(msg);
    setMessageType(type);
  };

  const load = () => {
    setLoading(true);
    // P3-3: request archived workspaces too so they can be purged. The
    // backend currently ignores `include_archived`, so archived rows won't
    // appear until it honors the param — see the implementation note in
    // the task report.
    fetchAdminWorkspaces({ include_archived: true })
      .then(setWorkspaces)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWsName.trim()) return;
    try {
      await createAdminWorkspace(newWsName.trim(), {
        slug: newWsSlug.trim() || undefined,
        description: newWsDescription.trim() || undefined,
        icon: newWsIcon.trim() || undefined,
      });
      setNewWsName("");
      setNewWsSlug("");
      setNewWsDescription("");
      setNewWsIcon("");
      setCreating(false);
      showMessage("Workspace created", "success");
      load();
    } catch (e: unknown) {
      showMessage(e instanceof Error ? e.message : "Failed to create workspace");
    }
  };

  const handleSave = async (id: string) => {
    try {
      await updateAdminWorkspace(id, {
        name: editName,
        slug: editSlug,
        description: editDescription,
        icon: editIcon,
        max_tokens_per_day: editTokens,
        max_cost_per_month: editCost,
      });
      setEditingId(null);
      load();
    } catch (e: unknown) {
      showMessage(String(e));
    }
  };

  const handleArchive = async (id: string, name: string) => {
    if (!confirm(`Archive workspace "${name}"?`)) return;
    try {
      await archiveWorkspace(id);
      showMessage("Workspace archived", "success");
      load();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      showMessage(msg);
    }
  };

  // P3-3: hard-delete an archived workspace. Requires the typed name to
  // exactly match — the submit button stays disabled until it does.
  const openPurge = (ws: AdminWorkspace) => {
    setPurgeWs(ws);
    setPurgeInput("");
  };

  const handlePurge = async () => {
    if (!purgeWs) return;
    if (purgeInput !== purgeWs.name) return;
    setPurging(true);
    try {
      await purgeWorkspace(purgeWs.id, purgeInput);
      showMessage("Workspace purged", "success");
      setPurgeWs(null);
      setPurgeInput("");
      load();
    } catch (e: unknown) {
      showMessage(e instanceof Error ? e.message : "Failed to purge workspace");
    } finally {
      setPurging(false);
    }
  };

  // P3-4: reversible — no confirmation modal needed.
  const handleSetDefault = async (id: string) => {
    setSettingDefault(id);
    try {
      await setDefaultWorkspace(id);
      showMessage("Default workspace updated", "success");
      load();
    } catch (e: unknown) {
      showMessage(e instanceof Error ? e.message : "Failed to set default workspace");
    } finally {
      setSettingDefault(null);
    }
  };

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
      showMessage(e instanceof Error ? e.message : "Failed to load members");
    } finally {
      setMemberLoading(false);
    }
  };

  const handleAddMember = async (userId: string, userName: string) => {
    if (!memberWsId) return;
    setAddingUser(userId);
    try {
      await addWorkspaceMember(memberWsId, userId, addRole);
      showMessage(`${userName} added`, "success");
      setSearchQuery("");
      setSearchResults([]);
      setShowDropdown(false);
      await openMembers(memberWsId);
      load(); // refresh to update member_count
    } catch (e: unknown) {
      showMessage(e instanceof Error ? e.message : "Failed to add member");
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
        // Filter out users already in this workspace
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

  const handleRemoveMember = async (userId: string, email: string) => {
    if (!memberWsId || !confirm(`Remove ${email} from this workspace?`)) return;
    try {
      await removeWorkspaceMember(memberWsId, userId);
      showMessage("Member removed", "success");
      await openMembers(memberWsId);
      load(); // refresh to update member_count
    } catch (e: unknown) {
      showMessage(e instanceof Error ? e.message : "Failed to remove member");
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Workspace Management</h1>
        <p className="page-subtitle">Manage workspaces, quotas, and settings</p>
      </div>

      {message && <div className={`alert alert-${messageType}`}>{message}</div>}

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header">
          <h3 className="card-title">Create Workspace</h3>
        </div>
        {creating ? (
          <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                value={newWsName}
                onChange={(e) => setNewWsName(e.target.value)}
                placeholder="Workspace name *"
                style={{ flex: 1 }}
                autoFocus
              />
              <input
                value={newWsIcon}
                onChange={(e) => setNewWsIcon(e.target.value)}
                placeholder="Icon (emoji or URL)"
                style={{ width: 180 }}
              />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                value={newWsSlug}
                onChange={(e) => setNewWsSlug(e.target.value)}
                placeholder="Slug (optional, auto-generated from name)"
                style={{ flex: 1 }}
              />
              <input
                value={newWsDescription}
                onChange={(e) => setNewWsDescription(e.target.value)}
                placeholder="Description (optional)"
                style={{ flex: 1 }}
              />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" className="btn btn-primary">Create</button>
              <button type="button" className="btn btn-secondary" onClick={() => { setCreating(false); setNewWsName(""); setNewWsSlug(""); setNewWsDescription(""); setNewWsIcon(""); }}>Cancel</button>
            </div>
          </form>
        ) : (
          <button className="btn btn-primary" onClick={() => setCreating(true)}>+ New Workspace</button>
        )}
      </div>

      {loading ? (
        <div className="loading">Loading workspaces</div>
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
                  <th>Owner</th>
                  <th>Created</th>
                  <th>Actions</th>
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
                      {editingId === ws.id ? (
                        <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                      ) : (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                          {ws.name}
                          {ws.is_default && (
                            <span className="badge badge-success" style={{ fontSize: "0.7rem" }}>Default</span>
                          )}
                          {ws.archived && (
                            <span className="badge badge-error" style={{ fontSize: "0.7rem" }}>Archived</span>
                          )}
                        </span>
                      )}
                    </td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {editingId === ws.id ? (
                        <input value={editSlug} onChange={(e) => setEditSlug(e.target.value)} placeholder="slug" style={{ width: 120 }} />
                      ) : (
                        ws.slug || ""
                      )}
                    </td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {editingId === ws.id ? (
                        <input value={editDescription} onChange={(e) => setEditDescription(e.target.value)} placeholder="description" style={{ width: 160 }} />
                      ) : (
                        ws.description || ""
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
                            <label style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginRight: 4 }}>Icon:</label>
                            <input value={editIcon} onChange={(e) => setEditIcon(e.target.value)} placeholder="emoji / URL" style={{ width: 120 }} />
                          </div>
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
                          {ws.archived ? (
                            // P3-3: archived workspaces can only be purged.
                            <button
                              className="btn btn-danger btn-sm"
                              onClick={() => openPurge(ws)}
                              title="Permanently delete this workspace and all its data"
                            >
                              Purge
                            </button>
                          ) : (
                            <>
                              <button className="btn btn-secondary btn-sm" onClick={() => { setEditingId(ws.id); setEditName(ws.name); setEditSlug(ws.slug || ""); setEditDescription(ws.description || ""); setEditIcon(ws.icon || ""); setEditTokens(0); setEditCost(0); }}>Edit</button>
                              <button className="btn btn-secondary btn-sm" onClick={() => openMembers(ws.id)}>Members</button>
                              {!ws.is_default && (
                                <button
                                  className="btn btn-secondary btn-sm"
                                  onClick={() => handleSetDefault(ws.id)}
                                  disabled={settingDefault === ws.id}
                                  title="Set as the default workspace for new users"
                                >
                                  {settingDefault === ws.id ? "Setting..." : "Set as default"}
                                </button>
                              )}
                              {!ws.is_default && (
                                <button className="btn btn-danger btn-sm" onClick={() => handleArchive(ws.id, ws.name)}>Archive</button>
                              )}
                            </>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Member management panel */}
          {memberWsId && (
            <div className="card" style={{ marginTop: 20 }}>
              <div className="card-header">
                <h3 className="card-title">
                  Members of {workspaces.find(w => w.id === memberWsId)?.name || memberWsId}
                </h3>
              </div>

              {memberLoading ? (
                <div className="loading" style={{ padding: 12 }}>Loading members...</div>
              ) : (
                <>
                  {members.length === 0 ? (
                    <p style={{ padding: 12, color: "var(--text-muted)" }}>No members yet.</p>
                  ) : (
                    <table>
                      <thead>
                        <tr>
                          <th>Email</th>
                          <th>Name</th>
                          <th>Role</th>
                          <th>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {members.map((m) => (
                          <tr key={m.user_id}>
                            <td>{m.email}</td>
                            <td>{m.name}</td>
                            <td><span className="badge badge-primary">{m.role}</span></td>
                            <td>
                              <button className="btn btn-danger btn-sm" onClick={() => handleRemoveMember(m.user_id, m.email)}>
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  <div style={{ padding: 12, borderTop: "1px solid var(--border)" }}>
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
                      <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Role</label>
                        <select value={addRole} onChange={e => setAddRole(e.target.value)}>
                          <option value="member">Member</option>
                          <option value="workspace_admin">Admin</option>
                          <option value="workspace_owner">Owner</option>
                          <option value="viewer">Viewer</option>
                        </select>
                      </div>
                      <button className="btn btn-secondary" onClick={() => setMemberWsId(null)} style={{ marginBottom: 0 }}>Close</button>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}

      {purgeWs && (
        <div className="modal-backdrop" onClick={() => !purging && setPurgeWs(null)}>
          <div className="card modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 480 }}>
            <div className="card-header">
              <h3 className="card-title">Purge workspace</h3>
            </div>
            <div className="alert alert-error" style={{ marginTop: 8 }}>
              This will permanently delete <strong>{purgeWs.name}</strong> and ALL its data
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
                placeholder={purgeWs.name}
                autoFocus
                style={{ fontFamily: "monospace" }}
              />
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button
                className="btn btn-danger"
                onClick={handlePurge}
                disabled={purging || purgeInput !== purgeWs.name}
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
          </div>
        </div>
      )}
    </div>
  );
}