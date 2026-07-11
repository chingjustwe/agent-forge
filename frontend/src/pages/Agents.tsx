import { useEffect, useState } from "react";
import {
  AdminWorkspace,
  AgentConfig,
  AgentFramework,
  copyAgentToWorkspace,
  createAgent,
  deleteAgent,
  fetchAdminWorkspaces,
  fetchAvailableGuardrails,
  fetchAvailableHooks,
  fetchAvailableSkills,
  fetchAvailableTools,
  fetchModels,
  getCurrentUser,
  GuardrailInfo,
  HookInfo,
  listAgents,
  listMCPServers,
  MCPServerInfo,
  ModelCatalog,
  SkillInfo,
  ToolInfo,
  updateAgent,
  User,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { Modal } from "../components/Modal";
import { Select } from "../components/Select";
import { Stepper, type Step as StepDef } from "../components/Stepper";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { Dropdown } from "../components/Dropdown";
import { SkeletonTable } from "../components/Skeleton";

// Wave 2.5: DirectLLM removed; deepagents is the sole framework.
// Dropdown kept (decision D3) so future adapters only need to extend
// ALLOWED_FRAMEWORKS + this array — no form restructuring required.
const FRAMEWORK_OPTIONS: { value: AgentFramework; label: string }[] = [
  { value: "deepagents", label: "DeepAgents" },
];

// 模型列表由后端在启动时从 LLM 厂商的 /v1/models 动态拉取（见
// src/infra/llm/models.py），前端不再硬编码，避免厂商实际模型
// （如 deepseek-v4-flash / deepseek-v4-pro）与页面显示不一致。
// MODEL_OPTIONS 改为从后端获取的 state（见 modelOptions）。

const EMPTY_FORM: AgentFormState = {
  name: "",
  framework: "deepagents",
  model: "",
  systemPrompt: "",
  temperature: "0.7",
  maxTokens: "",
  tools: [],
  memoryEnabled: false,
  memoryRecallTopK: "5",
  skills: [],
  guardrails: [],
  hooks: [],
  subagents: [],
  mcp_servers: [],
};

interface AgentFormState {
  name: string;
  framework: AgentFramework;
  model: string;
  systemPrompt: string;
  temperature: string;
  maxTokens: string;
  tools: string[];
  memoryEnabled: boolean;
  memoryRecallTopK: string;
  skills: string[];
  guardrails: string[];
  hooks: string[];
  /** Selected existing agent ids that act as subagents (deepagents only). */
  subagents: string[];
  /** MCP server names (workspace-scoped) this agent is bound to. */
  mcp_servers: string[];
}

// 向导分步定义：身份 → 角色 → 能力 → 治理（plan §2）
const WIZARD_STEPS: StepDef[] = [
  { id: 0, label: "Identity" },
  { id: 1, label: "Persona" },
  { id: 2, label: "Capabilities" },
  { id: 3, label: "Governance" },
];
const LAST_STEP = WIZARD_STEPS.length - 1;

/** 表单 → 请求体载荷：所有结构化字段作为顶层字段发送，而非塞进 config dict。 */
interface AgentPayload {
  name: string;
  framework: AgentFramework;
  model?: string;
  system_prompt?: string;
  temperature?: number;
  max_tokens?: number;
  tools?: string[];
  memory_config?: { enable_short_term: boolean; enable_long_term: boolean; recall_top_k: number } | null;
  skills?: string[];
  guardrails?: string[];
  hooks?: string[];
  subagents?: Array<{ agent_id: string }>;
  mcp_servers?: string[];
}

function formToPayload(form: AgentFormState): AgentPayload {
  const payload: AgentPayload = {
    name: form.name.trim(),
    framework: form.framework,
  };
  if (form.model.trim()) payload.model = form.model.trim();
  if (form.systemPrompt.trim()) payload.system_prompt = form.systemPrompt.trim();
  const t = parseFloat(form.temperature);
  if (!isNaN(t)) payload.temperature = t;
  const mt = parseInt(form.maxTokens, 10);
  if (!isNaN(mt) && mt > 0) payload.max_tokens = mt;
  // 工具白名单：始终发送（空数组表示清空）
  payload.tools = form.tools;
  // 内存配置
  payload.memory_config = form.memoryEnabled
    ? {
        enable_short_term: true,
        enable_long_term: true,
        recall_top_k: parseInt(form.memoryRecallTopK, 10) || 5,
      }
    : null;
  // Phase C: skills / guardrails / hooks / subagents — 始终发送（空数组表示清空）
  payload.skills = form.skills;
  payload.guardrails = form.guardrails;
  payload.hooks = form.hooks;
  // Subagents are references to existing agents (deepagents only).
  payload.subagents = form.subagents.map(id => ({ agent_id: id }));
  // MCP server binding: agent gets every tool exposed by each selected server.
  payload.mcp_servers = form.mcp_servers;
  return payload;
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
  // 向导当前步（plan §3.2）
  const [step, setStep] = useState(0);

  // Delete confirmation state
  const [deleteTarget, setDeleteTarget] = useState<AgentConfig | null>(null);

  // P3-2: cross-workspace copy (tenant_admin only).
  const [user, setUser] = useState<User | null>(null);
  const [copyAgent, setCopyAgent] = useState<AgentConfig | null>(null);
  const [targetWsId, setTargetWsId] = useState("");
  const [targetWorkspaces, setTargetWorkspaces] = useState<AdminWorkspace[]>([]);
  const [copying, setCopying] = useState(false);

  // Phase B: list of tools available for the multi-select whitelist.
  const [availableTools, setAvailableTools] = useState<ToolInfo[]>([]);
  // Phase C: lists for skills / guardrails / hooks multi-selects.
  const [availableSkills, setAvailableSkills] = useState<SkillInfo[]>([]);
  const [availableGuardrails, setAvailableGuardrails] = useState<GuardrailInfo[]>([]);
  const [availableHooks, setAvailableHooks] = useState<HookInfo[]>([]);
  // Phase 5: MCP servers available to bind to this agent.
  const [availableMCPServers, setAvailableMCPServers] = useState<MCPServerInfo[]>([]);

  // 动态模型列表：后端启动时从厂商 /v1/models 拉取，前端不再硬编码。
  const [modelOptions, setModelOptions] = useState<{ value: string; label: string }[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>("");

  const isTenantAdmin = user?.role === "tenant_admin";

  // Agents eligible to be used as subagents: every agent in the workspace
  // except the one currently being edited (self-reference is not allowed).
  const candidateSubagents = agents.filter(a => a.id !== editingId);

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

  // 拉取厂商动态模型列表（后端已在启动时从 /v1/models 缓存）。
  useEffect(() => {
    fetchModels()
      .then((cat: ModelCatalog) => {
        setModelOptions(cat.models.map(m => ({ value: m, label: m })));
        setDefaultModel(cat.default || "");
      })
      .catch(() => setModelOptions([]));
  }, []);

  // Phase B/C: when the create/edit modal opens, fetch the available tool /
  // skill / guardrail / hook lists used by the multi-selects.
  useEffect(() => {
    if (formModalOpen && currentWorkspaceId) {
      const ws = currentWorkspaceId;
      fetchAvailableTools(ws).then(setAvailableTools).catch(() => setAvailableTools([]));
      fetchAvailableSkills(ws).then(setAvailableSkills).catch(() => setAvailableSkills([]));
      fetchAvailableGuardrails(ws).then(setAvailableGuardrails).catch(() => setAvailableGuardrails([]));
      fetchAvailableHooks(ws).then(setAvailableHooks).catch(() => setAvailableHooks([]));
      listMCPServers(ws).then(setAvailableMCPServers).catch(() => setAvailableMCPServers([]));
    }
  }, [formModalOpen, currentWorkspaceId]);

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
    setForm({ ...EMPTY_FORM, model: defaultModel || "deepseek-v4-flash" });
    setEditingId(null);
    setStep(0);
    setFormModalOpen(true);
  }

  function startEdit(agent: AgentConfig) {
    setForm({
      name: agent.name,
      framework: agent.framework,
      model: agent.model || "",
      systemPrompt: agent.system_prompt || "",
      temperature: agent.temperature !== undefined ? String(agent.temperature) : "0.7",
      maxTokens: agent.max_tokens !== undefined ? String(agent.max_tokens) : "",
      tools: agent.tools || [],
      memoryEnabled: !!(agent.memory_config?.enable_long_term),
      memoryRecallTopK: String(agent.memory_config?.recall_top_k || 5),
      skills: agent.skills || [],
      guardrails: agent.guardrails || [],
      hooks: agent.hooks || [],
      subagents: (agent.subagents || [])
        .map(s => (typeof s.agent_id === "string" ? s.agent_id : ""))
        .filter(Boolean),
      mcp_servers: agent.mcp_servers || [],
    });
    setEditingId(agent.id);
    setStep(0);
    setFormModalOpen(true);
  }

  // 每步校验（plan §3.4）：Step 0 Name 必填；Step 1 temperature 范围 + maxTokens 正整数（非阻断式警告）
  function validateStep(s: number): boolean {
    if (s === 0) {
      if (!form.name.trim()) {
        toast.error("Validation error", "Name is required");
        return false;
      }
    }
    if (s === 1) {
      const t = parseFloat(form.temperature);
      if (!isNaN(t) && (t < 0 || t > 2)) {
        toast.error("Validation error", "Temperature must be between 0 and 2");
        return false;
      }
      const mt = parseInt(form.maxTokens, 10);
      if (form.maxTokens.trim() && (isNaN(mt) || mt <= 0)) {
        toast.error("Validation error", "Max Tokens must be a positive integer");
        return false;
      }
    }
    return true;
  }

  function handleNext() {
    if (!validateStep(step)) return;
    if (step < LAST_STEP) setStep(step + 1);
  }

  function handleBack() {
    if (step > 0) setStep(step - 1);
  }

  async function handleWizardSubmit() {
    // 最后一步提交前再校验一次当前步
    if (!validateStep(step)) return;
    await handleFormSubmit();
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
      const payload = formToPayload(form);
      if (editingId) {
        await updateAgent(currentWorkspaceId, editingId, payload);
        toast.success("Agent updated");
      } else {
        await createAgent(currentWorkspaceId, payload);
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
        <div className="agent-grid">
          {agents.map(agent => {
            const model = agent.model || "";
            const toolCount = agent.tools?.length || 0;
            const skillCount = agent.skills?.length || 0;
            const guardrailCount = agent.guardrails?.length || 0;
            const hasMemory = !!(agent.memory_config?.enable_long_term);
            return (
              <div key={agent.id} className="agent-card">
                <div className="agent-card-header">
                  <div className="agent-card-title-row">
                    <h3 className="agent-card-name">{agent.name}</h3>
                    {canManage && (
                      <Dropdown items={[
                        { label: "Edit", onClick: () => startEdit(agent) },
                        ...(isTenantAdmin ? [{ label: "Copy to...", onClick: () => openCopyModal(agent) }] : []),
                        { label: "Delete", onClick: () => openDeleteConfirm(agent), variant: "danger" },
                      ]} />
                    )}
                  </div>
                  <div className="agent-card-meta">
                    <span className="badge badge-primary">{agent.framework}</span>
                    <span className="agent-card-model">
                      {model || <em style={{ color: "var(--text-muted)" }}>no model</em>}
                    </span>
                  </div>
                </div>
                <div className="agent-card-chips">
                  {toolCount > 0 && <span className="agent-chip">🔧 {toolCount} tools</span>}
                  {skillCount > 0 && <span className="agent-chip">🧩 {skillCount} skills</span>}
                  {guardrailCount > 0 && <span className="agent-chip">🛡 {guardrailCount} guardrails</span>}
                  {hasMemory && <span className="agent-chip">💾 memory</span>}
                  {toolCount === 0 && skillCount === 0 && guardrailCount === 0 && !hasMemory && (
                    <span className="agent-chip agent-chip-muted">bare agent</span>
                  )}
                </div>
                <div className="agent-card-footer">
                  <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>
                    {formatDate(agent.created_at)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Create / Edit Modal — 分步向导（plan §3.2） */}
      <Modal
        open={formModalOpen}
        onClose={() => setFormModalOpen(false)}
        title={editingId ? "Edit Agent" : "Create Agent"}
        width="lg"
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setFormModalOpen(false)} disabled={saving}>
              Cancel
            </button>
            {step > 0 && (
              <button className="btn btn-secondary" onClick={handleBack} disabled={saving}>
                ← Back
              </button>
            )}
            {step < LAST_STEP ? (
              <button className="btn btn-primary" onClick={handleNext} disabled={saving}>
                Next →
              </button>
            ) : (
              <button className="btn btn-primary" onClick={handleWizardSubmit} disabled={saving}>
                {saving ? "Saving..." : editingId ? "Update Agent" : "Create Agent"}
              </button>
            )}
          </>
        }
      >
        <Stepper steps={WIZARD_STEPS} current={step} onJump={setStep} />
        <div className="wizard-body">
          {step === 0 && (
            <>
              <div className="form-group">
                <label className="form-label">Name <span style={{ color: "var(--text-muted)" }}>*</span></label>
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
                <Select
                  value={form.framework}
                  onChange={(v) => setForm({ ...form, framework: v as AgentFramework })}
                  options={FRAMEWORK_OPTIONS.map(o => ({ value: o.value, label: o.label }))}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Model</label>
                <Select
                  value={form.model}
                  onChange={(v) => setForm({ ...form, model: v })}
                  options={
                    modelOptions.some(o => o.value === form.model)
                      ? modelOptions
                      : form.model
                        ? [...modelOptions, { value: form.model, label: `${form.model} (custom)` }]
                        : modelOptions
                  }
                />
              </div>
            </>
          )}
          {step === 1 && (
            <>
              <div className="form-group">
                <label className="form-label">System Prompt</label>
                <textarea
                  value={form.systemPrompt}
                  onChange={e => setForm({ ...form, systemPrompt: e.target.value })}
                  rows={4}
                  placeholder="You are a helpful assistant."
                />
              </div>
              <div className="form-group">
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
              <div className="form-group">
                <label className="form-label">Max Tokens</label>
                <input
                  type="number"
                  min="1"
                  max="32768"
                  value={form.maxTokens}
                  onChange={e => setForm({ ...form, maxTokens: e.target.value })}
                  placeholder="4096"
                />
              </div>
            </>
          )}
          {step === 2 && (
            <>
              <div className="form-group">
                <label className="form-label">Tools</label>
                {availableTools.length === 0 ? (
                  <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", margin: "4px 0 0" }}>
                    No tools available for this workspace.
                  </p>
                ) : (
                  <ToolPicker
                    tools={availableTools}
                    selected={form.tools}
                    onToggle={(name) => {
                      const next = form.tools.includes(name)
                        ? form.tools.filter(t => t !== name)
                        : [...form.tools, name];
                      setForm({ ...form, tools: next });
                    }}
                  />
                )}
              </div>
              <div className="form-group">
                <label className="form-label">MCP Servers</label>
                <p style={{ color: "var(--text-secondary)", fontSize: "0.82rem", margin: "0 0 8px" }}>
                  Bind this agent to workspace MCP servers. The agent gets access to <strong>every tool</strong> exposed by each selected server (in addition to the tools chosen above).
                </p>
                {availableMCPServers.length === 0 ? (
                  <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", margin: "4px 0 0" }}>
                    No MCP servers registered in this workspace. Add one under Admin → MCP Servers.
                  </p>
                ) : (
                  <CheckList
                    items={availableMCPServers.map(s => ({ name: s.name, description: s.endpoint }))}
                    selected={form.mcp_servers}
                    onToggle={(name) => {
                      const next = form.mcp_servers.includes(name)
                        ? form.mcp_servers.filter(t => t !== name)
                        : [...form.mcp_servers, name];
                      setForm({ ...form, mcp_servers: next });
                    }}
                  />
                )}
              </div>
              <div className="form-group">
                <label className="form-label">Skills</label>
                {availableSkills.length === 0 ? (
                  <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", margin: "4px 0 0" }}>
                    No skills available for this workspace.
                  </p>
                ) : (
                  <CheckList
                    items={availableSkills.map(s => ({
                      name: s.name,
                      description: `${s.layer}${s.description ? " · " + s.description : ""}`,
                    }))}
                    selected={form.skills}
                    onToggle={(name) => {
                      const next = form.skills.includes(name)
                        ? form.skills.filter(t => t !== name)
                        : [...form.skills, name];
                      setForm({ ...form, skills: next });
                    }}
                  />
                )}
              </div>
            </>
          )}
          {step === 3 && (
            <>
              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={form.memoryEnabled}
                    onChange={e => setForm({ ...form, memoryEnabled: e.target.checked })}
                  />
                  Enable Long-Term Memory
                </label>
                {form.memoryEnabled && (
                  <div style={{ marginTop: 8 }}>
                    <label className="form-label">Recall Top-K</label>
                    <input
                      type="number"
                      min="1"
                      max="20"
                      value={form.memoryRecallTopK}
                      onChange={e => setForm({ ...form, memoryRecallTopK: e.target.value })}
                      placeholder="5"
                    />
                  </div>
                )}
              </div>
              {form.framework === "deepagents" && (
                <div className="form-group">
                  <label className="form-label">Subagents</label>
                  <p style={{ color: "var(--text-secondary)", fontSize: "0.82rem", margin: "0 0 8px" }}>
                    Select existing agents in this workspace to use as subagents.
                  </p>
                  {candidateSubagents.length === 0 ? (
                    <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", margin: "4px 0 0" }}>
                      No other agents available in this workspace.
                    </p>
                  ) : (
                    <div className="check-list">
                      {candidateSubagents.map(a => {
                        const checked = form.subagents.includes(a.id);
                        return (
                          <label
                            key={a.id}
                            className={`check-row${checked ? " selected" : ""}`}
                            title={a.name}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => {
                                const next = checked
                                  ? form.subagents.filter(id => id !== a.id)
                                  : [...form.subagents, a.id];
                                setForm({ ...form, subagents: next });
                              }}
                            />
                            <span className="check-meta">
                              <span className="check-name">{a.name}</span>
                              <span className="check-desc">
                                {a.framework}{a.model ? ` · ${a.model}` : ""}
                              </span>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              <div className="form-group">
                <label className="form-label">Guardrails</label>
                {availableGuardrails.length === 0 ? (
                  <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", margin: "4px 0 0" }}>
                    No guardrails available for this workspace.
                  </p>
                ) : (
                  <CheckList
                    items={availableGuardrails.map(g => ({ name: g.name, description: g.description }))}
                    selected={form.guardrails}
                    onToggle={(name) => {
                      const next = form.guardrails.includes(name)
                        ? form.guardrails.filter(t => t !== name)
                        : [...form.guardrails, name];
                      setForm({ ...form, guardrails: next });
                    }}
                  />
                )}
              </div>
              <div className="form-group">
                <label className="form-label">Hooks</label>
                {availableHooks.length === 0 ? (
                  <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", margin: "4px 0 0" }}>
                    No hooks available for this workspace.
                  </p>
                ) : (
                  <CheckList
                    items={availableHooks.map(h => ({ name: h.name, description: h.description }))}
                    selected={form.hooks}
                    onToggle={(name) => {
                      const next = form.hooks.includes(name)
                        ? form.hooks.filter(t => t !== name)
                        : [...form.hooks, name];
                      setForm({ ...form, hooks: next });
                    }}
                  />
                )}
              </div>
            </>
          )}
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
            <Select
              value={targetWsId}
              onChange={setTargetWsId}
              options={targetWorkspaces.map(w => ({ value: w.id, label: w.name }))}
            />
          )}
        </div>
      </Modal>
    </div>
  );
}

// Phase B: grouped, multi-select tool whitelist.
// MCP tools are excluded — they're controlled by the "MCP Servers" section
// below. Memory tools (save_memory/recall_memory) are excluded — they're
// controlled by the "Enable Long-Term Memory" toggle.
const MANAGED_TOOLS = new Set(["save_memory", "recall_memory"]);

const SOURCE_ORDER: ToolInfo["source"][] = ["builtin", "custom"];
const SOURCE_LABELS: Record<ToolInfo["source"], string> = {
  builtin: "Builtin",
  mcp: "MCP",
  custom: "Custom",
};

function ToolPicker({
  tools,
  selected,
  onToggle,
}: {
  tools: ToolInfo[];
  selected: string[];
  onToggle: (name: string) => void;
}) {
  // Filter out MCP tools (managed by MCP Servers section) and managed tools
  // (memory tools, controlled by the Long-Term Memory toggle).
  const visible = tools.filter(
    t => t.source !== "mcp" && !MANAGED_TOOLS.has(t.name),
  );
  const grouped: Record<string, ToolInfo[]> = {};
  for (const t of visible) {
    (grouped[t.source] ||= []).push(t);
  }
  return (
    <div className="tool-picker">
      {SOURCE_ORDER.filter(src => grouped[src]?.length).map(src => (
        <div key={src} className="tool-group">
          <div className="tool-group-title">
            <span className={`badge badge-source-${src}`}>{SOURCE_LABELS[src]}</span>
            <span className="tool-group-count">{grouped[src].length}</span>
          </div>
          {grouped[src].map(tool => {
            const checked = selected.includes(tool.name);
            return (
              <label key={tool.name} className={`tool-row${checked ? " selected" : ""}`}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggle(tool.name)}
                />
                <span className="tool-meta">
                  <span className="tool-name" title={tool.name}>{tool.name}</span>
                  <span className="tool-desc" title={tool.description || undefined}>{tool.description || "—"}</span>
                </span>
              </label>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// Phase C: generic checkbox list (skills / guardrails / hooks).
function CheckList({
  items,
  selected,
  onToggle,
}: {
  items: { name: string; description?: string }[];
  selected: string[];
  onToggle: (name: string) => void;
}) {
  return (
    <div className="check-list">
      {items.map(item => {
        const checked = selected.includes(item.name);
        return (
          <label
            key={item.name}
            className={`check-row${checked ? " selected" : ""}`}
            title={item.name}
          >
            <input
              type="checkbox"
              checked={checked}
              onChange={() => onToggle(item.name)}
            />
            <span className="check-meta">
              <span className="check-name">{item.name}</span>
              {item.description && (
                <span className="check-desc">{item.description}</span>
              )}
            </span>
          </label>
        );
      })}
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
