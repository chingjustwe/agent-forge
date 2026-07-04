import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from "react";
import { listWorkspaces, createWorkspace, listAdminUsers } from "../api";
export default function AdminPage() {
    const [workspaces, setWorkspaces] = useState([]);
    const [users, setUsers] = useState([]);
    const [newWsName, setNewWsName] = useState("");
    const [error, setError] = useState("");
    async function loadData() {
        try {
            const [ws, us] = await Promise.all([listWorkspaces(), listAdminUsers()]);
            setWorkspaces(ws);
            setUsers(us);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load data");
        }
    }
    useEffect(() => {
        loadData();
    }, []);
    async function handleCreateWorkspace(e) {
        e.preventDefault();
        if (!newWsName.trim())
            return;
        try {
            await createWorkspace(newWsName.trim());
            setNewWsName("");
            await loadData();
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to create workspace");
        }
    }
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Admin Overview" }), _jsx("p", { className: "page-subtitle", children: "Manage workspaces, users, and platform settings" })] }), error && _jsx("div", { className: "alert alert-error", children: error }), _jsxs("div", { className: "stat-grid", children: [_jsxs("div", { className: "stat-card stat-card-accent", children: [_jsx("div", { className: "stat-card-value", children: workspaces.length }), _jsx("div", { className: "stat-card-label", children: "Workspaces" })] }), _jsxs("div", { className: "stat-card stat-card-accent-success", children: [_jsx("div", { className: "stat-card-value", children: users.length }), _jsx("div", { className: "stat-card-label", children: "Users" })] })] }), _jsxs("div", { className: "admin-nav", children: [_jsx("a", { href: "/admin/users", className: "admin-nav-link", children: "\uD83D\uDC65 Manage Users" }), _jsx("a", { href: "/admin/workspaces", className: "admin-nav-link", children: "\uD83C\uDFE2 Manage Workspaces" }), _jsx("a", { href: "/admin/audit", className: "admin-nav-link", children: "\uD83D\uDCDD Audit Log" }), _jsx("a", { href: "/admin/usage", className: "admin-nav-link", children: "\uD83D\uDCC8 Usage" })] }), _jsxs("section", { style: { marginTop: 32 }, children: [_jsx("h2", { className: "detail-section-title", children: "Workspaces" }), _jsxs("form", { onSubmit: handleCreateWorkspace, style: { display: "flex", gap: 8, marginBottom: 16 }, children: [_jsx("input", { value: newWsName, onChange: (e) => setNewWsName(e.target.value), placeholder: "New workspace name", style: { maxWidth: 300 } }), _jsx("button", { type: "submit", className: "btn btn-primary", children: "Create" })] }), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Name" }), _jsx("th", { children: "Members" }), _jsx("th", { children: "Created" })] }) }), _jsx("tbody", { children: workspaces.map((ws) => (_jsxs("tr", { children: [_jsx("td", { children: ws.name }), _jsx("td", { children: ws.member_count ?? 0 }), _jsx("td", { style: { color: "var(--text-secondary)", fontSize: "0.82rem" }, children: new Date(ws.created_at).toLocaleDateString() })] }, ws.id))) })] }) })] }), _jsxs("section", { style: { marginTop: 32 }, children: [_jsx("h2", { className: "detail-section-title", children: "Users" }), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Email" }), _jsx("th", { children: "Name" }), _jsx("th", { children: "Role" }), _jsx("th", { children: "Workspaces" })] }) }), _jsx("tbody", { children: users.map((u) => (_jsxs("tr", { children: [_jsx("td", { children: u.email }), _jsx("td", { children: u.name }), _jsx("td", { children: _jsx("span", { className: "badge badge-primary", children: u.role }) }), _jsx("td", { children: u.workspace_count ?? (u.workspace_ids?.length ?? 0) })] }, u.id))) })] }) })] })] }));
}
