import { useEffect, useState } from "react";
import {
  AdminWorkspace,
  AgentConfig,
  AgentFramework,
  copyAgentToWorkspace,
  createAgent,
  deleteAgent,
  fetchAdminWorkspaces,
  getCurrentUser,
  listAgents,
  updateAgent,
  User,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { Dropdown } from "../components/Dropdown";
import { SkeletonTable } from "../components/Skeleton";

const FRAMEWORK_OPTIONS: { value: AgentFramework; label: string }[] = [
  { value: "direct_llm", label: "Direct LLM" },
  { value: "adk", label: "Google ADK" },
  { value: "langgraph", label: "LangGraph" },
];

const EMPTY_FORM: AgentFormState = {
  name: "",
  framework: "direct_llm",
  model: "",
  systemPrompt: "",
  temperature: "0.7",
};

interface AgentFormState {
  name: string;
  framework: AgentFramework;
  model: string;
  systemPrompt: string;
  temperature: string;
}

function formToConfig(form: AgentFormState): Record<string, unknown> {
  const cfg: Record<string, unknown> = {};
  if (form.model.trim()) cfg.model = form.model.trim();
  if (form.systemPrompt.trim()) cfg.system_prompt = form.systemPrompt.trim();
  const t = parseFloat(form.temperature);
  if (!isNaN(t)) cfg.temperature = t;
  return cfg;
}

function configFields(config: Record<string, unknown>): { model: string; systemPrompt: string; temperature: string } {
  return {
    model: typeof config.model === "string" ? config.model : "",
    systemPrompt: typeof config.system_prompt === "string" ? config.system_prompt : "",
    temperature: typeof config.temperature === "number" ? String(config.temperature) : "0.7",
  };
}

export default function Agents() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const toast = useToast();

  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create / edit modal state
  const [formModalOpen, setFormModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<AgentFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  // Delete confirmation state
  const [deleteTarget, setDeleteTarget] = useState<AgentConfig | null>(null);

  // P3-2: cross-workspace copy (tenant_admin only).
  const [user, setUser] = useState<User | null>(null);
  const [copyAgent, setCopyAgent] = useState<AgentConfig | null>(null);
  const [targetWsId, setTargetWsId] = useState("");
  const [targetWorkspaces, setTargetWorkspaces] = useState<AdminWorkspace[]>([]);
  const [copying, setCopying] = useState(false);

  const isTenantAdmin = user?.role === "tenant_admin";

  const canManage =
    currentRole === "workspace_admin" ||
    currentRole === "tenant_admin";

  async function refresh() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const list = await listAgents(currentWorkspaceId);
      setAgents(list);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => {});
  }, []);

  // P3-2: load the target-workspace list when the copy modal opens.
  async function openCopyModal(agent: AgentConfig) {
    setCopyAgent(agent);
    setTargetWsId("");
    try {
      const list = await fetchAdminWorkspaces();
      // Exclude the current workspace; archived workspaces can't be a copy
      // target either.
      const eligible = list.filter(
        w => w.id !== currentWorkspaceId && !w.archived,
      );
      setTargetWorkspaces(eligible);
      setTargetWsId(eligible[0]?.id || "");
    } catch (e: unknown) {
      toast.error("Failed to load workspaces", e instanceof Error ? e.message : undefined);
    }
  }

  async function handleCopySubmit() {
    if (!copyAgent || !currentWorkspaceId || !targetWsId) return;
    setCopying(true);
    try {
      await copyAgentToWorkspace(currentWorkspaceId, copyAgent.id, targetWsId);
      const targetName =
        targetWorkspaces.find(w => w.id === targetWsId)?.name || targetWsId;
      toast.success("Agent copied", `Copied to ${targetName}`);
      setCopyAgent(null);
    } catch (err: unknown) {
      toast.error("Failed to copy agent", err instanceof Error ? err.message : undefined);
    } finally {
      setCopying(false);
    }
  }

  function openCreateModal() {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setFormModalOpen(true);
  }

  function startEdit(agent: AgentConfig) {
    const cfg = configFields(agent.config || {});
    setForm({
      name: agent.name,
      framework: agent.framework,
      model: cfg.model,
      systemPrompt: cfg.systemPrompt,
      temperature: cfg.temperature,
    });
    setEditingId(agent.id);
    setFormModalOpen(true);
  }

  function openDeleteConfirm(agent: AgentConfig) {
    setDeleteTarget(agent);
  }

  async function handleFormSubmit() {
    if (!currentWorkspaceId) return;
    if (!form.name.trim()) {
      toast.error("Validation error", "Name is required");
      return;
    }
    setSaving(true);
    try {
      const config = formToConfig(form);
      if (editingId) {
        await updateAgent(currentWorkspaceId, editingId, {
          name: form.name.trim(),
          framework: form.framework,
          config,
        });
        toast.success("Agent updated");
      } else {
        await createAgent(currentWorkspaceId, {
          name: form.name.trim(),
          framework: form.framework,
          config,
        });
        toast.success("Agent created");
      }
      setFormModalOpen(false);
      setEditingId(null);
      setForm(EMPTY_FORM);
      await refresh();
    } catch (err: unknown) {
      toast.error("Failed to save agent", err instanceof Error ? err.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteConfirm() {
    if (!deleteTarget || !currentWorkspaceId) return;
    try {
      await deleteAgent(currentWorkspaceId, deleteTarget.id);
      setAgents(prev => prev.filter(a => a.id !== deleteTarget.id));
      toast.success("Agent deleted");
      setDeleteTarget(null);
    } catch (err: unknown) {
      toast.error("Failed to delete agent", err instanceof Error ? err.message : undefined);
    }
  }

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Agents</h1>
          <p className="page-subtitle">Workspace-scoped agent configurations</p>
        </div>
        <EmptyState
          title="No workspace selected"
          description="Pick one from the sidebar."
        />
      </div>
    );
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="page-title">Agents</h1>
          <p className="page-subtitle">Manage agent configurations bound to this workspace</p>
        </div>
        {canManage && (
          <button className="btn btn-primary" onClick={openCreateModal}>
            + New Agent
          </button>
        )}
      </div>

      {error && <EmptyState title="Error loading agents" description={error} />}

      {!error && loading && <SkeletonTable rows={5} cols={5} />}

      {!error && !loading && agents.length === 0 && (
        <EmptyState
          title="No agents yet"
          description={canManage ? "Create your first agent to get started." : "No agents have been configured for this workspace."}
          action={canManage ? { label: "+ New Agent", onClick: openCreateModal } : undefined}
        />
      )}

      {!error && !loading && agents.length > 0 && (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Framework</th>
                <th>Model</th>
                <th>Created</th>
                {canManage && <th style={{ width: 1 }}>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {agents.map(agent => {
                const model = typeof agent.config?.model === "string" ? agent.config.model : "";
                return (
                  <tr key={agent.id}>
                    <td>{agent.name}</td>
                    <td><span className="badge badge-primary">{agent.framework}</span></td>
                    <td>{model || <em style={{ color: "var(--text-muted)" }}>&mdash;</em>}</td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {formatDate(agent.created_at)}
                    </td>
                    {canManage && (
                      <td>
                        <Dropdown items={[
                          { label: "Edit", onClick: () => startEdit(agent) },
                          ...(isTenantAdmin ? [{ label: "Copy to...", onClick: () => openCopyModal(agent) }] : []),
                          { label: "Delete", onClick: () => openDeleteConfirm(agent), variant: "danger" },
                        ]} />
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Create / Edit Modal */}
      <Modal
        open={formModalOpen}
        onClose={() => setFormModalOpen(false)}
        title={editingId ? "Edit Agent" : "Create Agent"}
        width="md"
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setFormModalOpen(false)} disabled={saving}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={handleFormSubmit} disabled={saving}>
              {saving ? "Saving..." : editingId ? "Update Agent" : "Create Agent"}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="form-group">
            <label className="form-label">Name</label>
            <input
              type="text"
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              maxLength={100}
              placeholder="e.g. Support Bot"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Framework</label>
            <select
              value={form.framework}
              onChange={e => setForm({ ...form, framework: e.target.value as AgentFramework })}
            >
              {FRAMEWORK_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Model</label>
            <input
              type="text"
              value={form.model}
              onChange={e => setForm({ ...form, model: e.target.value })}
              placeholder="e.g. deepseek-chat, gpt-4"
            />
          </div>
          <div className="form-group">
            <label className="form-label">System Prompt</label>
            <textarea
              value={form.systemPrompt}
              onChange={e => setForm({ ...form, systemPrompt: e.target.value })}
              rows={4}
              placeholder="You are a helpful assistant."
            />
          </div>
          <div className="form-group" style={{ width: 160 }}>
            <label className="form-label">Temperature</label>
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={form.temperature}
              onChange={e => setForm({ ...form, temperature: e.target.value })}
            />
          </div>
        </div>
      </Modal>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        title="Delete Agent"
        description={`Delete agent "${deleteTarget?.name}"? This cannot be undone.`}
        confirmText="Delete"
        variant="danger"
      />

      {/* Cross-workspace Copy Modal */}
      <Modal
        open={!!copyAgent}
        onClose={() => !copying && setCopyAgent(null)}
        title="Copy Agent"
        width="sm"
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setCopyAgent(null)} disabled={copying}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={handleCopySubmit}
              disabled={copying || !targetWsId}
            >
              {copying ? "Copying..." : "Copy"}
            </button>
          </>
        }
      >
        <p style={{ color: "var(--text-secondary)", fontSize: "0.88rem", marginBottom: 12 }}>
          Copies <strong>{copyAgent?.name}</strong> into the selected workspace. The original is left untouched.
        </p>
        <div className="form-group">
          <label className="form-label">Target workspace</label>
          {targetWorkspaces.length === 0 ? (
            <div style={{ color: "var(--text-secondary)", fontSize: "0.88rem" }}>
              No other workspaces available to copy to.
            </div>
          ) : (
            <select
              value={targetWsId}
              onChange={e => setTargetWsId(e.target.value)}
              autoFocus
            >
              {targetWorkspaces.map(w => (
                <option key={w.id} value={w.id}>{w.name}</option>
              ))}
            </select>
          )}
        </div>
      </Modal>
    </div>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "-";
  try {
    const normalized = /([Z]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : iso + "Z";
    const d = new Date(normalized);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}
