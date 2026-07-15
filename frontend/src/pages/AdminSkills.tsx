import { useEffect, useState } from "react";
import {
  createSkill,
  deleteSkill,
  fetchAvailableSkills,
  fetchPermissions,
  fetchSkill,
  getCurrentUser,
  reloadSkill,
  SkillDetail,
  SkillInfo,
  SkillLayer,
  updateSkill,
  User,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { SkeletonTable } from "../components/Skeleton";

const LAYER_ORDER: SkillLayer[] = ["workspace", "project", "user"];

const LAYER_META: Record<SkillLayer, { label: string; badge: string; subtitle: string }> = {
  workspace: {
    label: "Workspace Skills",
    badge: "badge-success",
    subtitle: "Writable skills stored for this workspace",
  },
  project: {
    label: "Project Skills",
    badge: "badge-info",
    subtitle: "Read-only skills from agents/skills",
  },
  user: {
    label: "User Skills",
    badge: "badge-muted",
    subtitle: "Read-only skills from the user skill directory",
  },
};

interface SkillForm {
  name: string;
  description: string;
  instructions: string;
  tools: string;
  required_memory: boolean;
  version: string;
}

const EMPTY_FORM: SkillForm = {
  name: "",
  description: "",
  instructions: "",
  tools: "",
  required_memory: false,
  version: "1.0",
};

export default function AdminSkills() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const toast = useToast();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloading, setReloading] = useState<string | null>(null);
  const [canWrite, setCanWrite] = useState(false);
  const [user, setUser] = useState<User | null>(null);

  const isWorkspaceAdmin =
    currentRole === "workspace_admin" || currentRole === "tenant_admin";

  /** Whether the current user may edit/delete a specific workspace skill. */
  function canEditSkill(skill: SkillInfo): boolean {
    // workspace_admin / tenant_admin can edit any skill.
    if (isWorkspaceAdmin) return true;
    // Directory-layer skills are read-only for everyone (handled by editable
    // flag elsewhere); for workspace skills, the user must be the owner.
    if (!skill.editable) return false;
    return skill.created_by === (user?.id || "");
  }

  // Detail modal
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Add / Edit modal
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null); // null = create
  const [form, setForm] = useState<SkillForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<SkillInfo | null>(null);

  async function refresh() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const list = await fetchAvailableSkills(currentWorkspaceId);
      setSkills(list);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load skills");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId]);

  useEffect(() => {
    fetchPermissions()
      .then(p => setCanWrite(p.permissions.includes("*") || p.permissions.includes("skills:write")))
      .catch(() => setCanWrite(false));
    getCurrentUser().then(setUser).catch(() => {});
  }, []);

  async function handleReload(name: string) {
    if (!currentWorkspaceId) return;
    setReloading(name);
    try {
      await reloadSkill(currentWorkspaceId, name);
      toast.success("Skill reloaded", name);
    } catch (e: unknown) {
      toast.error("Failed to reload", e instanceof Error ? e.message : undefined);
    } finally {
      setReloading(null);
    }
  }

  async function openDetail(name: string) {
    if (!currentWorkspaceId) return;
    setDetail(null);
    setDetailLoading(true);
    try {
      const d = await fetchSkill(currentWorkspaceId, name);
      setDetail(d);
    } catch (e: unknown) {
      toast.error("Failed to load skill", e instanceof Error ? e.message : undefined);
    } finally {
      setDetailLoading(false);
    }
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setFormOpen(true);
  }

  async function openEdit(name: string) {
    if (!currentWorkspaceId) return;
    try {
      const d = await fetchSkill(currentWorkspaceId, name);
      setEditing(name);
      setForm({
        name: d.name,
        description: d.description || "",
        instructions: d.instructions || "",
        tools: (d.tools || []).join(", "),
        required_memory: !!d.required_memory,
        version: d.version || "1.0",
      });
      setFormOpen(true);
    } catch (e: unknown) {
      toast.error("Failed to load skill", e instanceof Error ? e.message : undefined);
    }
  }

  async function handleSave() {
    if (!currentWorkspaceId) return;
    if (!editing && !/^[a-z0-9_-]+$/.test(form.name)) {
      toast.error("Invalid name", "Only lowercase letters, digits, - and _ are allowed");
      return;
    }
    const tools = form.tools.split(",").map(t => t.trim()).filter(Boolean);
    const payload = {
      description: form.description,
      instructions: form.instructions,
      tools,
      required_memory: form.required_memory,
      version: form.version,
    };
    setSaving(true);
    try {
      if (editing) {
        await updateSkill(currentWorkspaceId, editing, payload);
        toast.success("Skill updated", editing);
      } else {
        await createSkill(currentWorkspaceId, { name: form.name, ...payload });
        toast.success("Skill created", form.name);
      }
      setFormOpen(false);
      refresh();
    } catch (e: unknown) {
      toast.error("Failed to save skill", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget || !currentWorkspaceId) return;
    try {
      await deleteSkill(currentWorkspaceId, deleteTarget.name);
      toast.success("Skill deleted", deleteTarget.name);
      setDeleteTarget(null);
      refresh();
    } catch (e: unknown) {
      toast.error("Failed to delete", e instanceof Error ? e.message : undefined);
    }
  }

  const grouped: Record<SkillLayer, SkillInfo[]> = {
    workspace: [],
    project: [],
    user: [],
  };
  for (const s of skills) {
    (grouped[s.layer] ?? grouped.project).push(s);
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="page-title">Skills</h1>
          <p className="page-subtitle">Skills aggregated from user, project, and workspace layers</p>
        </div>
        {canWrite && (
          <button className="btn btn-primary" onClick={openCreate}>+ Add Skill</button>
        )}
      </div>

      {error && <EmptyState title="Error loading skills" description={error} />}
      {!error && loading && <SkeletonTable rows={5} cols={5} />}
      {!error && !loading && skills.length === 0 && (
        <EmptyState
          title="No skills"
          description={
            canWrite
              ? "No skills yet. Add a workspace skill, or place markdown files under agents/skills."
              : "No skills are registered."
          }
          action={canWrite ? { label: "+ Add Skill", onClick: openCreate } : undefined}
        />
      )}

      {!error && !loading && skills.length > 0 && LAYER_ORDER.map(layer => {
        const rows = grouped[layer];
        if (rows.length === 0) return null;
        const meta = LAYER_META[layer];
        const readOnly = layer !== "workspace";
        return (
          <div key={layer} style={{ marginBottom: 28 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
              <h2 style={{ fontSize: "1rem", margin: 0 }}>{meta.label}</h2>
              <span className={`badge ${meta.badge}`}>{layer}</span>
              {readOnly && <span className="badge badge-muted">read-only</span>}
              <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>{meta.subtitle}</span>
            </div>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Layer</th>
                    <th>Description</th>
                    <th>Version</th>
                    <th>Tools</th>
                    <th style={{ width: 1 }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(skill => (
                    <tr key={`${skill.layer}-${skill.name}`}>
                      <td style={{ fontWeight: 600, fontFamily: "var(--font-mono, monospace)" }}>{skill.name}</td>
                      <td>
                        <span className={`badge ${LAYER_META[skill.layer].badge}`}>{skill.layer}</span>
                      </td>
                      <td style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>{skill.description || "—"}</td>
                      <td><span className="badge badge-muted">{skill.version || "—"}</span></td>
                      <td>
                        {skill.tools && skill.tools.length > 0
                          ? skill.tools.map(t => <span key={t} className="badge badge-info" style={{ marginRight: 4 }}>{t}</span>)
                          : <span style={{ color: "var(--text-muted)" }}>—</span>}
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button className="btn btn-secondary btn-sm" onClick={() => openDetail(skill.name)}>View</button>
                          {canEditSkill(skill) ? (
                            <>
                              <button className="btn btn-secondary btn-sm" onClick={() => openEdit(skill.name)}>Edit</button>
                              <button className="btn btn-danger btn-sm" onClick={() => setDeleteTarget(skill)}>Delete</button>
                            </>
                          ) : canWrite && !skill.editable ? (
                            <button
                              className="btn btn-secondary btn-sm"
                              disabled={reloading === skill.name}
                              onClick={() => handleReload(skill.name)}
                            >
                              {reloading === skill.name ? "Reloading..." : "Reload"}
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}

      {/* Detail Modal */}
      <Modal
        open={!!detail || detailLoading}
        onClose={() => setDetail(null)}
        title={detail ? `Skill: ${detail.name}` : "Skill"}
        width="lg"
        footer={<button className="btn btn-secondary" onClick={() => setDetail(null)}>Close</button>}
      >
        {detailLoading && !detail ? (
          <p style={{ color: "var(--text-secondary)" }}>Loading skill content...</p>
        ) : detail ? (
          <div>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.88rem", marginTop: 0 }}>{detail.description}</p>
            <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
              <span className={`badge ${LAYER_META[detail.layer].badge}`}>{detail.layer}</span>
              {!detail.editable && <span className="badge badge-muted">read-only</span>}
              <span className="badge badge-muted">v{detail.version || "?"}</span>
              {detail.required_memory && <span className="badge badge-warning">requires memory</span>}
              {detail.tools?.map(t => <span key={t} className="badge badge-info">{t}</span>)}
            </div>
            <label className="form-label">Instructions</label>
            <pre className="skill-instructions">{detail.instructions || "(no instructions)"}</pre>
          </div>
        ) : null}
      </Modal>

      {/* Add / Edit Modal */}
      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={editing ? `Edit Skill: ${editing}` : "Add Skill"}
        width="lg"
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setFormOpen(false)} disabled={saving}>Cancel</button>
            <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : editing ? "Save" : "Create"}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="form-group">
            <label className="form-label">Name *</label>
            <input
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. my-ws-skill"
              disabled={!!editing}
              autoFocus={!editing}
            />
            {!editing && (
              <p style={{ color: "var(--text-muted)", fontSize: "0.75rem", margin: "4px 0 0" }}>
                Lowercase letters, digits, - and _ only.
              </p>
            )}
          </div>
          <div className="form-group">
            <label className="form-label">Description</label>
            <input
              value={form.description}
              onChange={e => setForm({ ...form, description: e.target.value })}
              placeholder="Short summary"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Instructions</label>
            <textarea
              value={form.instructions}
              onChange={e => setForm({ ...form, instructions: e.target.value })}
              placeholder="Markdown instructions injected into the system prompt"
              rows={10}
              style={{ fontFamily: "var(--font-mono, monospace)", fontSize: "0.85rem" }}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Tools (comma-separated)</label>
            <input
              value={form.tools}
              onChange={e => setForm({ ...form, tools: e.target.value })}
              placeholder="tool_a, tool_b"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Version</label>
            <input
              value={form.version}
              onChange={e => setForm({ ...form, version: e.target.value })}
              placeholder="1.0"
            />
          </div>
          <div className="form-group">
            <label className="check-row" style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={form.required_memory}
                onChange={e => setForm({ ...form, required_memory: e.target.checked })}
              />
              <span className="check-meta">
                <span className="check-name">Requires memory</span>
                <span className="check-desc">Mark if this skill depends on long-term memory.</span>
              </span>
            </label>
          </div>
        </div>
      </Modal>

      {/* Delete ConfirmDialog */}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="Delete Skill"
        description={`Remove "${deleteTarget?.name}"? Agents referencing it will no longer receive its instructions.`}
        confirmText="Delete"
        variant="danger"
      />
    </div>
  );
}
