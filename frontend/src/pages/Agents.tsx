import { useEffect, useState } from "react";
import {
  AgentConfig,
  AgentFramework,
  createAgent,
  deleteAgent,
  listAgents,
  updateAgent,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";

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
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<"error" | "success">("error");

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<AgentFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const canManage =
    currentRole === "workspace_admin" ||
    currentRole === "workspace_owner" ||
    currentRole === "tenant_admin";

  function showMsg(msg: string, type: "error" | "success" = "error") {
    setMessage(msg);
    setMessageType(type);
  }

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

  function resetForm() {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setShowForm(false);
  }

  function startCreate() {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setShowForm(true);
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
    setShowForm(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!currentWorkspaceId) return;
    if (!form.name.trim()) {
      showMsg("Name is required");
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const config = formToConfig(form);
      if (editingId) {
        await updateAgent(currentWorkspaceId, editingId, {
          name: form.name.trim(),
          framework: form.framework,
          config,
        });
        showMsg("Agent updated", "success");
      } else {
        await createAgent(currentWorkspaceId, {
          name: form.name.trim(),
          framework: form.framework,
          config,
        });
        showMsg("Agent created", "success");
      }
      resetForm();
      await refresh();
    } catch (err: unknown) {
      showMsg(err instanceof Error ? err.message : "Failed to save agent");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(agent: AgentConfig) {
    if (!currentWorkspaceId) return;
    if (!confirm(`Delete agent "${agent.name}"? This cannot be undone.`)) return;
    try {
      await deleteAgent(currentWorkspaceId, agent.id);
      setAgents(prev => prev.filter(a => a.id !== agent.id));
      showMsg("Agent deleted", "success");
    } catch (err: unknown) {
      showMsg(err instanceof Error ? err.message : "Failed to delete agent");
    }
  }

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Agents</h1>
          <p className="page-subtitle">Workspace-scoped agent configurations</p>
        </div>
        <div className="alert alert-info">No workspace selected. Pick one from the sidebar.</div>
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
          <button className="btn btn-primary" onClick={() => (showForm ? resetForm() : startCreate())}>
            {showForm && !editingId ? "Cancel" : "+ New Agent"}
          </button>
        )}
      </div>

      {message && <div className={`alert alert-${messageType}`}>{message}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      {showForm && canManage && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card-header">
            <h3 className="card-title">{editingId ? "Edit Agent" : "Create Agent"}</h3>
          </div>
          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
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
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? "Saving..." : editingId ? "Update Agent" : "Create Agent"}
              </button>
              <button type="button" className="btn btn-secondary" onClick={resetForm}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <div className="alert alert-info">Loading agents...</div>
      ) : agents.length === 0 ? (
        <div className="alert alert-info">
          No agents yet. {canManage && "Create your first agent."}
        </div>
      ) : (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Framework</th>
                <th>Model</th>
                <th>Created</th>
                <th style={{ width: 1 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {agents.map(agent => {
                const model = typeof agent.config?.model === "string" ? agent.config.model : "";
                return (
                  <tr key={agent.id}>
                    <td>{agent.name}</td>
                    <td><span className="badge badge-primary">{agent.framework}</span></td>
                    <td>{model || <em style={{ color: "var(--text-muted)" }}>—</em>}</td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {formatDate(agent.created_at)}
                    </td>
                    <td>
                      {canManage ? (
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            className="btn btn-secondary"
                            style={{ padding: "4px 10px", fontSize: "0.78rem" }}
                            onClick={() => startEdit(agent)}
                          >
                            Edit
                          </button>
                          <button
                            className="btn btn-danger"
                            style={{ padding: "4px 10px", fontSize: "0.78rem" }}
                            onClick={() => handleDelete(agent)}
                          >
                            Delete
                          </button>
                        </div>
                      ) : (
                        <em style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>read-only</em>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
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
