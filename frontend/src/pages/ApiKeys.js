import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { createApiKey, fetchPermissions, listApiKeys, revokeApiKey, } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
const EMPTY_FORM = {
    name: "",
    scopes: ["chat:write"],
    expiresInDays: "",
};
function scopeLabel(scope) {
    const [resource, action] = scope.split(":");
    return `${resource} (${action})`;
}
export default function ApiKeys() {
    const { currentWorkspaceId, currentRole } = useWorkspace();
    const [keys, setKeys] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [message, setMessage] = useState(null);
    const [messageType, setMessageType] = useState("error");
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);
    // Plaintext key from a freshly-created API key — shown once in a modal.
    const [newKey, setNewKey] = useState(null);
    const [copied, setCopied] = useState(false);
    const [availableScopes, setAvailableScopes] = useState([]);
    const canManage = currentRole === "workspace_admin" ||
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
            const list = await listApiKeys(currentWorkspaceId);
            setKeys(list);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load API keys");
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
        fetchPermissions()
            .then(resp => setAvailableScopes(resp.api_key_scopes || []))
            .catch(() => { });
    }, []);
    function resetForm() {
        setForm(EMPTY_FORM);
        setShowForm(false);
    }
    function toggleScope(scope) {
        setForm(prev => ({
            ...prev,
            scopes: prev.scopes.includes(scope)
                ? prev.scopes.filter(s => s !== scope)
                : [...prev.scopes, scope],
        }));
    }
    async function handleSubmit(e) {
        e.preventDefault();
        if (!currentWorkspaceId)
            return;
        if (!form.name.trim()) {
            showMsg("Name is required");
            return;
        }
        if (form.scopes.length === 0) {
            showMsg("Select at least one scope");
            return;
        }
        const days = form.expiresInDays.trim();
        const expiresInDays = days === "" ? undefined : parseInt(days, 10);
        if (expiresInDays !== undefined && (isNaN(expiresInDays) || expiresInDays < 1 || expiresInDays > 365)) {
            showMsg("Expires in days must be between 1 and 365 (or leave blank for no expiry)");
            return;
        }
        setSaving(true);
        setMessage(null);
        try {
            const created = await createApiKey(currentWorkspaceId, {
                name: form.name.trim(),
                scopes: form.scopes,
                expires_in_days: expiresInDays,
            });
            setNewKey(created.key);
            setCopied(false);
            resetForm();
            await refresh();
            showMsg("API key created", "success");
        }
        catch (err) {
            showMsg(err instanceof Error ? err.message : "Failed to create API key");
        }
        finally {
            setSaving(false);
        }
    }
    async function copyNewKey() {
        if (!newKey)
            return;
        try {
            await navigator.clipboard.writeText(newKey);
            setCopied(true);
        }
        catch {
            // Clipboard may be unavailable (e.g. insecure context); fall back to
            // selecting the text input so the user can manually copy.
            setCopied(false);
        }
    }
    async function handleRevoke(key) {
        if (!currentWorkspaceId)
            return;
        if (!confirm(`Revoke API key "${key.name}"? It will stop working immediately.`))
            return;
        try {
            await revokeApiKey(currentWorkspaceId, key.id);
            setKeys(prev => prev.filter(k => k.id !== key.id));
            showMsg("API key revoked", "success");
        }
        catch (err) {
            showMsg(err instanceof Error ? err.message : "Failed to revoke API key");
        }
    }
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "API Keys" }), _jsx("p", { className: "page-subtitle", children: "Workspace-scoped API keys for programmatic access" })] }), _jsx("div", { className: "alert alert-info", children: "No workspace selected. Pick one from the sidebar." })] }));
    }
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start" }, children: [_jsxs("div", { children: [_jsx("h1", { className: "page-title", children: "API Keys" }), _jsx("p", { className: "page-subtitle", children: "Manage API keys bound to this workspace" })] }), canManage && (_jsx("button", { className: "btn btn-primary", onClick: () => (showForm ? resetForm() : setShowForm(true)), children: showForm ? "Cancel" : "+ New API Key" }))] }), message && _jsx("div", { className: `alert alert-${messageType}`, children: message }), error && _jsx("div", { className: "alert alert-error", children: error }), showForm && canManage && (_jsxs("div", { className: "card", style: { marginBottom: 20 }, children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: "Create API Key" }) }), _jsxs("form", { onSubmit: handleSubmit, style: { display: "flex", flexDirection: "column", gap: 10 }, children: [_jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Name" }), _jsx("input", { type: "text", value: form.name, onChange: e => setForm({ ...form, name: e.target.value }), maxLength: 100, placeholder: "e.g. CI pipeline key" })] }), _jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Scopes" }), _jsx("div", { style: { display: "flex", flexWrap: "wrap", gap: 12 }, children: availableScopes.map(s => (_jsxs("label", { style: { display: "flex", alignItems: "center", gap: 6, fontSize: "0.9rem" }, children: [_jsx("input", { type: "checkbox", checked: form.scopes.includes(s), onChange: () => toggleScope(s) }), scopeLabel(s)] }, s))) })] }), _jsxs("div", { className: "form-group", style: { width: 200 }, children: [_jsx("label", { className: "form-label", children: "Expires in days (blank = never)" }), _jsx("input", { type: "number", min: 1, max: 365, value: form.expiresInDays, onChange: e => setForm({ ...form, expiresInDays: e.target.value }), placeholder: "never" })] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("button", { type: "submit", className: "btn btn-primary", disabled: saving, children: saving ? "Creating..." : "Create API Key" }), _jsx("button", { type: "button", className: "btn btn-secondary", onClick: resetForm, children: "Cancel" })] })] })] })), loading ? (_jsx("div", { className: "alert alert-info", children: "Loading API keys..." })) : keys.length === 0 ? (_jsxs("div", { className: "alert alert-info", children: ["No API keys yet. ", canManage && "Create your first key."] })) : (_jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Name" }), _jsx("th", { children: "Prefix" }), _jsx("th", { children: "Scopes" }), _jsx("th", { children: "Status" }), _jsx("th", { children: "Last used" }), _jsx("th", { children: "Expires" }), _jsx("th", { children: "Created" }), canManage && _jsx("th", { style: { width: 1 }, children: "Actions" })] }) }), _jsx("tbody", { children: keys.map(k => {
                                const status = keyStatus(k);
                                return (_jsxs("tr", { children: [_jsx("td", { children: k.name }), _jsx("td", { children: _jsxs("code", { style: { fontSize: "0.85rem" }, children: [k.key_prefix, "\u2026"] }) }), _jsx("td", { children: _jsx("div", { style: { display: "flex", flexWrap: "wrap", gap: 4 }, children: (k.scopes || []).map(s => (_jsx("span", { className: "badge badge-primary", style: { fontSize: "0.72rem" }, children: s }, s))) }) }), _jsx("td", { children: _jsx("span", { className: `badge badge-${status.tone}`, children: status.label }) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: formatDate(k.last_used_at) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: k.expires_at ? formatDate(k.expires_at) : "never" }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: formatDate(k.created_at) }), canManage && (_jsx("td", { children: !k.revoked && (_jsx("button", { className: "btn btn-danger", style: { padding: "4px 10px", fontSize: "0.78rem" }, onClick: () => handleRevoke(k), children: "Revoke" })) }))] }, k.id));
                            }) })] }) })), newKey && (_jsx("div", { className: "modal-backdrop", onClick: () => setNewKey(null), children: _jsxs("div", { className: "card modal-card", onClick: e => e.stopPropagation(), style: { maxWidth: 560 }, children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: "Copy your API key" }) }), _jsx("div", { className: "alert alert-warning", style: { marginTop: 8 }, children: "Copy this key now. You won\u2019t be able to see it again." }), _jsx("div", { className: "form-group", style: { marginTop: 12 }, children: _jsx("textarea", { readOnly: true, value: newKey, rows: 2, style: { fontFamily: "monospace", fontSize: "0.9rem" }, onFocus: e => e.target.select() }) }), _jsxs("div", { style: { display: "flex", gap: 8, marginTop: 8 }, children: [_jsx("button", { className: "btn btn-primary", onClick: copyNewKey, children: copied ? "Copied!" : "Copy" }), _jsx("button", { className: "btn btn-secondary", onClick: () => setNewKey(null), children: "Done" })] })] }) }))] }));
}
function keyStatus(k) {
    if (k.revoked)
        return { label: "Revoked", tone: "error" };
    if (k.expires_at) {
        try {
            const normalized = /([Z]|[+-]\d{2}:?\d{2})$/.test(k.expires_at) ? k.expires_at : k.expires_at + "Z";
            if (new Date(normalized) < new Date())
                return { label: "Expired", tone: "error" };
        }
        catch {
            // fall through to active
        }
    }
    return { label: "Active", tone: "success" };
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
