import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getObservabilityRequests } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
export default function RequestList() {
    const { currentWorkspaceId } = useWorkspace();
    const [requests, setRequests] = useState([]);
    const [filter, setFilter] = useState("");
    const navigate = useNavigate();
    useEffect(() => {
        if (!currentWorkspaceId)
            return;
        getObservabilityRequests(currentWorkspaceId, { limit: 100 }).then(setRequests);
    }, [currentWorkspaceId]);
    const filtered = requests.filter(r => !filter || r.model?.includes(filter) || r.status_code === Number(filter));
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Request List" }), _jsx("p", { className: "page-subtitle", children: "Browse and inspect API requests to your agents" })] }), _jsx("div", { className: "alert alert-info", children: "No workspace selected. Please select a workspace from the top bar." })] }));
    }
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Request List" }), _jsx("p", { className: "page-subtitle", children: "Browse and inspect API requests to your agents" })] }), _jsx("div", { className: "filter-bar", children: _jsx("input", { placeholder: "Filter by model or status...", value: filter, onChange: e => setFilter(e.target.value), style: { minWidth: 240 } }) }), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Model" }), _jsx("th", { children: "Status" }), _jsx("th", { children: "Duration (ms)" }), _jsx("th", { children: "Error" }), _jsx("th", { children: "Created" })] }) }), _jsxs("tbody", { children: [filtered.map(r => (_jsxs("tr", { onClick: () => navigate(`/requests/${r.trace_id}`), className: "clickable", children: [_jsx("td", { children: r.model || "-" }), _jsx("td", { children: _jsx("span", { className: `badge ${r.status_code >= 400 ? "badge-error" : "badge-success"}`, children: r.status_code }) }), _jsx("td", { style: { fontFamily: "var(--font-mono)", fontSize: "0.82rem" }, children: r.duration_ms }), _jsx("td", { style: { color: r.error ? "var(--error)" : "var(--text-muted)" }, children: r.error || "-" }), _jsx("td", { style: { color: "var(--text-secondary)", fontSize: "0.82rem" }, children: r.created_at })] }, r.id))), filtered.length === 0 && (_jsx("tr", { children: _jsx("td", { colSpan: 5, style: { textAlign: "center", padding: 32, color: "var(--text-muted)" }, children: "No requests found" }) }))] })] }) })] }));
}
