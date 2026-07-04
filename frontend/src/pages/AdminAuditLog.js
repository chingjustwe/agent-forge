import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { fetchAdminAudit } from "../api";
const ACTIONS = [
    "",
    "workspace.create",
    "workspace.update",
    "workspace.delete",
    "user.role_change",
    "user.invite",
    "user.delete",
];
export default function AdminAuditLog() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [action, setAction] = useState("");
    const [userId, setUserId] = useState("");
    const [since, setSince] = useState("");
    const [until, setUntil] = useState("");
    const [offset, setOffset] = useState(0);
    const [expandedId, setExpandedId] = useState(null);
    const limit = 20;
    const load = () => {
        setLoading(true);
        fetchAdminAudit({
            action: action || undefined,
            user_id: userId || undefined,
            since: since || undefined,
            until: until || undefined,
            limit,
            offset,
        })
            .then(setData)
            .finally(() => setLoading(false));
    };
    useEffect(() => { load(); }, [offset]);
    const totalPages = data ? Math.ceil(data.total / limit) : 0;
    const currentPage = Math.floor(offset / limit) + 1;
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Audit Log" }), _jsx("p", { className: "page-subtitle", children: "Track administrative actions across the platform" })] }), _jsxs("div", { className: "filter-bar", children: [_jsx("select", { value: action, onChange: (e) => setAction(e.target.value), children: ACTIONS.map((a) => (_jsx("option", { value: a, children: a || "All actions" }, a))) }), _jsx("input", { placeholder: "User ID", value: userId, onChange: (e) => setUserId(e.target.value), style: { minWidth: 200 } }), _jsx("input", { type: "date", value: since, onChange: (e) => setSince(e.target.value) }), _jsx("input", { type: "date", value: until, onChange: (e) => setUntil(e.target.value) }), _jsx("button", { className: "btn btn-secondary", onClick: load, children: "Filter" })] }), loading ? (_jsx("div", { className: "loading", children: "Loading audit log" })) : (_jsxs(_Fragment, { children: [_jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Timestamp" }), _jsx("th", { children: "User" }), _jsx("th", { children: "Action" }), _jsx("th", { children: "Target" }), _jsx("th", { children: "IP" })] }) }), _jsxs("tbody", { children: [data?.items.map((entry) => (_jsxs(_Fragment, { children: [_jsxs("tr", { onClick: () => setExpandedId(expandedId === entry.id ? null : entry.id), className: "clickable", children: [_jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: new Date(entry.created_at).toLocaleString() }), _jsx("td", { style: { fontFamily: "var(--font-mono)", fontSize: "0.82rem" }, children: entry.user_id }), _jsx("td", { children: _jsx("span", { className: "badge badge-info", children: entry.action }) }), _jsxs("td", { style: { fontSize: "0.82rem" }, children: [entry.target_type, ":", entry.target_id] }), _jsx("td", { style: { fontFamily: "var(--font-mono)", fontSize: "0.78rem", color: "var(--text-muted)" }, children: entry.ip_address })] }, entry.id), expandedId === entry.id && (_jsx("tr", { className: "expanded-row", children: _jsx("td", { colSpan: 5, children: _jsxs("div", { className: "expanded-content", children: [_jsx("strong", { children: "Details" }), _jsx("pre", { children: JSON.stringify(entry.details, null, 2) })] }) }) }))] }))), (!data?.items || data.items.length === 0) && (_jsx("tr", { children: _jsx("td", { colSpan: 5, style: { textAlign: "center", padding: 32, color: "var(--text-muted)" }, children: "No audit entries found" }) }))] })] }) }), data && (_jsxs("div", { className: "pagination", children: [_jsx("button", { className: "btn btn-secondary btn-sm", disabled: offset <= 0, onClick: () => setOffset(Math.max(0, offset - limit)), children: "Previous" }), _jsxs("span", { className: "pagination-info", children: ["Page ", currentPage, " of ", totalPages || 1, " (", data.total, " total)"] }), _jsx("button", { className: "btn btn-secondary btn-sm", disabled: offset + limit >= data.total, onClick: () => setOffset(offset + limit), children: "Next" })] }))] }))] }));
}
