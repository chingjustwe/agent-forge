import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { getOtelSettings, updateOtelSettings } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
export default function Settings() {
    const { currentWorkspaceId, currentRole } = useWorkspace();
    const [config, setConfig] = useState({ enabled: false, endpoint: "", headers: {} });
    const [headersText, setHeadersText] = useState("{}");
    const [saved, setSaved] = useState(false);
    // Workspace-level admin (or tenant_admin) can edit settings.
    const canEdit = currentRole === "workspace_admin" ||
        currentRole === "workspace_owner";
    useEffect(() => {
        if (!currentWorkspaceId)
            return;
        getOtelSettings(currentWorkspaceId).then(data => {
            setConfig(data);
            setHeadersText(JSON.stringify(data.headers, null, 2));
        });
    }, [currentWorkspaceId]);
    const handleSave = async () => {
        if (!currentWorkspaceId)
            return;
        try {
            const headers = JSON.parse(headersText);
            await updateOtelSettings(currentWorkspaceId, { ...config, headers });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        }
        catch {
            alert("Invalid JSON in headers");
        }
    };
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Settings" }), _jsx("p", { className: "page-subtitle", children: "Configure workspace integrations and preferences" })] }), _jsx("div", { className: "alert alert-info", children: "No workspace selected. Please select a workspace from the top bar." })] }));
    }
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Settings" }), _jsx("p", { className: "page-subtitle", children: "Configure workspace integrations and preferences" })] }), _jsxs("div", { className: "card settings-section", children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: "OpenTelemetry Export" }) }), _jsx("div", { className: "form-group", children: _jsxs("label", { style: { display: "flex", alignItems: "center", gap: 8, cursor: canEdit ? "pointer" : "default" }, children: [_jsx("input", { type: "checkbox", checked: config.enabled, onChange: e => canEdit && setConfig({ ...config, enabled: e.target.checked }), disabled: !canEdit, style: { width: "auto" } }), _jsx("span", { style: { fontSize: "0.88rem", color: "var(--text-primary)" }, children: "Enabled" })] }) }), _jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Endpoint URL" }), _jsx("input", { type: "text", value: config.endpoint, onChange: e => canEdit && setConfig({ ...config, endpoint: e.target.value }), disabled: !canEdit, placeholder: "http://otel-collector:4318" })] }), _jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Headers (JSON)" }), _jsx("textarea", { value: headersText, onChange: e => canEdit && setHeadersText(e.target.value), disabled: !canEdit, rows: 4 })] }), canEdit && (_jsx("button", { className: "btn btn-primary", onClick: handleSave, children: saved ? "Saved!" : "Save Settings" }))] })] }));
}
