import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { copyAgentToWorkspace, createAgent, deleteAgent, fetchAdminWorkspaces, getCurrentUser, listAgents, updateAgent, } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
const FRAMEWORK_OPTIONS = [
    { value: "direct_llm", label: "Direct LLM" },
    { value: "adk", label: "Google ADK" },
    { value: "langgraph", label: "LangGraph" },
];
const EMPTY_FORM = {
    name: "",
    framework: "direct_llm",
    model: "",
    systemPrompt: "",
    temperature: "0.7",
};
function formToConfig(form) {
    const cfg = {};
    if (form.model.trim())
        cfg.model = form.model.trim();
    if (form.systemPrompt.trim())
        cfg.system_prompt = form.systemPrompt.trim();
    const t = parseFloat(form.temperature);
    if (!isNaN(t))
        cfg.temperature = t;
    return cfg;
}
function configFields(config) {
    return {
        model: typeof config.model === "string" ? config.model : "",
        systemPrompt: typeof config.system_prompt === "string" ? config.system_prompt : "",
        temperature: typeof config.temperature === "number" ? String(config.temperature) : "0.7",
    };
}
export default function Agents() {
    const { currentWorkspaceId, currentRole } = useWorkspace();
    const [agents, setAgents] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [message, setMessage] = useState(null);
    const [messageType, setMessageType] = useState("error");
    const [showForm, setShowForm] = useState(false);
    const [editingId, setEditingId] = useState(null);
    const [form, setForm] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);
    // P3-2: cross-workspace copy (tenant_admin only).
    const [user, setUser] = useState(null);
    const [copyAgent, setCopyAgent] = useState(null);
    const [targetWsId, setTargetWsId] = useState("");
    const [targetWorkspaces, setTargetWorkspaces] = useState([]);
    const [copying, setCopying] = useState(false);
    const isTenantAdmin = user?.role === "tenant_admin";
    const canManage = currentRole === "workspace_admin" ||
        currentRole === "workspace_owner" ||
        currentRole === "tenant_admin";
    function showMsg(msg, type = "error") {
        setMessage(msg);
        setMessageType(type);
    }
    async function refresh() {
        if (!currentWorkspaceId)
            return;
        setLoading(true);
        setError(null);
        try {
            const list = await listAgents(currentWorkspaceId);
            setAgents(list);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load agents");
        }
        finally {
            setLoading(false);
        }
    }
    useEffect(() => {
        refresh();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentWorkspaceId]);
    useEffect(() => {
        getCurrentUser().then(setUser).catch(() => { });
    }, []);
    // P3-2: load the target-workspace list when the copy modal opens.
    async function openCopyModal(agent) {
        setCopyAgent(agent);
        setTargetWsId("");
        try {
            const list = await fetchAdminWorkspaces();
            // Exclude the current workspace; archived workspaces can't be a copy
            // target either.
            const eligible = list.filter(w => w.id !== currentWorkspaceId && !w.archived);
            setTargetWorkspaces(eligible);
            setTargetWsId(eligible[0]?.id || "");
        }
        catch (e) {
            showMsg(e instanceof Error ? e.message : "Failed to load workspaces");
        }
    }
    async function handleCopySubmit() {
        if (!copyAgent || !currentWorkspaceId || !targetWsId)
            return;
        setCopying(true);
        setMessage(null);
        try {
            await copyAgentToWorkspace(currentWorkspaceId, copyAgent.id, targetWsId);
            const targetName = targetWorkspaces.find(w => w.id === targetWsId)?.name || targetWsId;
            showMsg(`Agent copied to ${targetName}`, "success");
            setCopyAgent(null);
        }
        catch (err) {
            showMsg(err instanceof Error ? err.message : "Failed to copy agent");
        }
        finally {
            setCopying(false);
        }
    }
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
    function startEdit(agent) {
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
    async function handleSubmit(e) {
        e.preventDefault();
        if (!currentWorkspaceId)
            return;
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
            }
            else {
                await createAgent(currentWorkspaceId, {
                    name: form.name.trim(),
                    framework: form.framework,
                    config,
                });
                showMsg("Agent created", "success");
            }
            resetForm();
            await refresh();
        }
        catch (err) {
            showMsg(err instanceof Error ? err.message : "Failed to save agent");
        }
        finally {
            setSaving(false);
        }
    }
    async function handleDelete(agent) {
        if (!currentWorkspaceId)
            return;
        if (!confirm(`Delete agent "${agent.name}"? This cannot be undone.`))
            return;
        try {
            await deleteAgent(currentWorkspaceId, agent.id);
            setAgents(prev => prev.filter(a => a.id !== agent.id));
            showMsg("Agent deleted", "success");
        }
        catch (err) {
            showMsg(err instanceof Error ? err.message : "Failed to delete agent");
        }
    }
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Agents" }), _jsx("p", { className: "page-subtitle", children: "Workspace-scoped agent configurations" })] }), _jsx("div", { className: "alert alert-info", children: "No workspace selected. Pick one from the sidebar." })] }));
    }
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start" }, children: [_jsxs("div", { children: [_jsx("h1", { className: "page-title", children: "Agents" }), _jsx("p", { className: "page-subtitle", children: "Manage agent configurations bound to this workspace" })] }), canManage && (_jsx("button", { className: "btn btn-primary", onClick: () => (showForm ? resetForm() : startCreate()), children: showForm && !editingId ? "Cancel" : "+ New Agent" }))] }), message && _jsx("div", { className: `alert alert-${messageType}`, children: message }), error && _jsx("div", { className: "alert alert-error", children: error }), showForm && canManage && (_jsxs("div", { className: "card", style: { marginBottom: 20 }, children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: editingId ? "Edit Agent" : "Create Agent" }) }), _jsxs("form", { onSubmit: handleSubmit, style: { display: "flex", flexDirection: "column", gap: 10 }, children: [_jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Name" }), _jsx("input", { type: "text", value: form.name, onChange: e => setForm({ ...form, name: e.target.value }), maxLength: 100, placeholder: "e.g. Support Bot" })] }), _jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Framework" }), _jsx("select", { value: form.framework, onChange: e => setForm({ ...form, framework: e.target.value }), children: FRAMEWORK_OPTIONS.map(o => (_jsx("option", { value: o.value, children: o.label }, o.value))) })] }), _jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Model" }), _jsx("input", { type: "text", value: form.model, onChange: e => setForm({ ...form, model: e.target.value }), placeholder: "e.g. deepseek-chat, gpt-4" })] }), _jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "System Prompt" }), _jsx("textarea", { value: form.systemPrompt, onChange: e => setForm({ ...form, systemPrompt: e.target.value }), rows: 4, placeholder: "You are a helpful assistant." })] }), _jsxs("div", { className: "form-group", style: { width: 160 }, children: [_jsx("label", { className: "form-label", children: "Temperature" }), _jsx("input", { type: "number", step: "0.1", min: "0", max: "2", value: form.temperature, onChange: e => setForm({ ...form, temperature: e.target.value }) })] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("button", { type: "submit", className: "btn btn-primary", disabled: saving, children: saving ? "Saving..." : editingId ? "Update Agent" : "Create Agent" }), _jsx("button", { type: "button", className: "btn btn-secondary", onClick: resetForm, children: "Cancel" })] })] })] })), loading ? (_jsx("div", { className: "alert alert-info", children: "Loading agents..." })) : agents.length === 0 ? (_jsxs("div", { className: "alert alert-info", children: ["No agents yet. ", canManage && "Create your first agent."] })) : (_jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Name" }), _jsx("th", { children: "Framework" }), _jsx("th", { children: "Model" }), _jsx("th", { children: "Created" }), _jsx("th", { style: { width: 1 }, children: "Actions" })] }) }), _jsx("tbody", { children: agents.map(agent => {
                                const model = typeof agent.config?.model === "string" ? agent.config.model : "";
                                return (_jsxs("tr", { children: [_jsx("td", { children: agent.name }), _jsx("td", { children: _jsx("span", { className: "badge badge-primary", children: agent.framework }) }), _jsx("td", { children: model || _jsx("em", { style: { color: "var(--text-muted)" }, children: "\u2014" }) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: formatDate(agent.created_at) }), _jsx("td", { children: canManage ? (_jsxs("div", { style: { display: "flex", gap: 6 }, children: [_jsx("button", { className: "btn btn-secondary", style: { padding: "4px 10px", fontSize: "0.78rem" }, onClick: () => startEdit(agent), children: "Edit" }), isTenantAdmin && (_jsx("button", { className: "btn btn-secondary", style: { padding: "4px 10px", fontSize: "0.78rem" }, onClick: () => openCopyModal(agent), title: "Copy this agent to another workspace", children: "Copy to..." })), _jsx("button", { className: "btn btn-danger", style: { padding: "4px 10px", fontSize: "0.78rem" }, onClick: () => handleDelete(agent), children: "Delete" })] })) : (_jsx("em", { style: { color: "var(--text-muted)", fontSize: "0.78rem" }, children: "read-only" })) })] }, agent.id));
                            }) })] }) })), copyAgent && (_jsx("div", { className: "modal-backdrop", onClick: () => !copying && setCopyAgent(null), children: _jsxs("div", { className: "card modal-card", onClick: e => e.stopPropagation(), style: { maxWidth: 480 }, children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: "Copy agent to\u2026" }) }), _jsxs("p", { style: { color: "var(--text-secondary)", fontSize: "0.88rem", margin: "4px 0 12px" }, children: ["Copies ", _jsx("strong", { children: copyAgent.name }), " into the selected workspace. The original is left untouched."] }), _jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Target workspace" }), targetWorkspaces.length === 0 ? (_jsx("div", { className: "alert alert-info", style: { margin: 0 }, children: "No other workspaces available to copy to." })) : (_jsx("select", { value: targetWsId, onChange: e => setTargetWsId(e.target.value), autoFocus: true, children: targetWorkspaces.map(w => (_jsx("option", { value: w.id, children: w.name }, w.id))) }))] }), _jsxs("div", { style: { display: "flex", gap: 8, marginTop: 8 }, children: [_jsx("button", { className: "btn btn-primary", onClick: handleCopySubmit, disabled: copying || !targetWsId, children: copying ? "Copying..." : "Copy" }), _jsx("button", { className: "btn btn-secondary", onClick: () => setCopyAgent(null), disabled: copying, children: "Cancel" })] })] }) }))] }));
}
function formatDate(iso) {
    if (!iso)
        return "-";
    try {
        const normalized = /([Z]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : iso + "Z";
        const d = new Date(normalized);
        if (isNaN(d.getTime()))
            return iso;
        return d.toLocaleString();
    }
    catch {
        return iso;
    }
}
