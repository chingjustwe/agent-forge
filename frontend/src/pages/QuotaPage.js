import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { getQuota, updateQuota } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
export default function QuotaPage() {
    const { currentWorkspaceId, currentRole } = useWorkspace();
    const [quota, setQuota] = useState(null);
    const [editTokens, setEditTokens] = useState(0);
    const [editing, setEditing] = useState(false);
    const [error, setError] = useState(null);
    // Workspace-level admin (or tenant_admin) can edit quota.
    const canEdit = currentRole === "workspace_admin";
    useEffect(() => {
        if (!currentWorkspaceId) {
            setError("No workspace selected. Please select a workspace from the top bar.");
            return;
        }
        setError(null);
        getQuota(currentWorkspaceId)
            .then(data => {
            setQuota(data);
            setEditTokens(data.max_tokens_per_day);
        })
            .catch(err => {
            setError(err instanceof Error ? err.message : "Failed to load quota data");
        });
    }, [currentWorkspaceId]);
    const handleSave = async () => {
        if (!currentWorkspaceId)
            return;
        try {
            await updateQuota(currentWorkspaceId, { max_tokens_per_day: editTokens });
            setEditing(false);
            const data = await getQuota(currentWorkspaceId);
            setQuota(data);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to update quota");
        }
    };
    if (error)
        return _jsx("div", { className: "alert alert-error", children: error });
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Quota Management" }), _jsx("p", { className: "page-subtitle", children: "Monitor and configure token usage limits" })] }), _jsx("div", { className: "alert alert-info", children: "No workspace selected. Please select a workspace from the top bar." })] }));
    }
    if (!quota)
        return _jsx("div", { className: "loading", children: "Loading quota data" });
    const pct = quota.max_tokens_per_day > 0
        ? Math.min(100, (quota.tokens_used / quota.max_tokens_per_day) * 100)
        : 0;
    const barClass = pct > 90 ? "progress-bar-fill-error" : pct > 70 ? "progress-bar-fill-warning" : "";
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Quota Management" }), _jsx("p", { className: "page-subtitle", children: "Monitor and configure token usage limits" })] }), _jsxs("div", { className: "stat-grid", style: { gridTemplateColumns: "1fr 1fr" }, children: [_jsxs("div", { className: "stat-card stat-card-accent", children: [_jsx("div", { className: "stat-card-value", children: quota.tokens_used.toLocaleString() }), _jsx("div", { className: "stat-card-label", children: "Tokens Used Today" })] }), _jsxs("div", { className: "stat-card", children: [_jsxs("div", { className: "stat-card-value", children: ["$", quota.cost_today.toFixed(4)] }), _jsx("div", { className: "stat-card-label", children: "Cost Today" })] })] }), _jsxs("div", { className: "card", style: { marginBottom: 20 }, children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: "Today's Usage" }) }), _jsx("div", { className: "progress-bar", children: _jsx("div", { className: `progress-bar-fill ${barClass}`, style: { width: `${pct}%` } }) }), _jsxs("p", { className: "quota-usage-text", children: [quota.tokens_used.toLocaleString(), " / ", quota.max_tokens_per_day === 0 ? "Unlimited" : quota.max_tokens_per_day.toLocaleString(), " tokens"] })] }), canEdit && (_jsxs("div", { className: "card", children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: "Configuration" }) }), editing ? (_jsxs("div", { children: [_jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Max Tokens Per Day" }), _jsx("input", { type: "number", value: editTokens, onChange: e => setEditTokens(Number(e.target.value)) })] }), _jsxs("div", { className: "btn-group", children: [_jsx("button", { className: "btn btn-primary", onClick: handleSave, children: "Save" }), _jsx("button", { className: "btn btn-secondary", onClick: () => setEditing(false), children: "Cancel" })] })] })) : (_jsxs("div", { className: "quota-config", children: [_jsxs("p", { children: ["Max tokens/day: ", _jsx("strong", { children: quota.max_tokens_per_day.toLocaleString() })] }), _jsxs("p", { children: ["Max cost/month: ", _jsxs("strong", { children: ["$", quota.max_cost_per_month.toFixed(2)] })] }), _jsx("button", { className: "btn btn-secondary btn-sm", onClick: () => setEditing(true), style: { marginTop: 8 }, children: "Edit Limits" })] }))] }))] }));
}
