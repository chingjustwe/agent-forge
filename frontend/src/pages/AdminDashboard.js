import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { fetchTenants, fetchUsers, fetchAdminWorkspaces, fetchUsage } from "../api";
export default function AdminDashboard() {
    const [tenants, setTenants] = useState([]);
    const [users, setUsers] = useState([]);
    const [workspaces, setWorkspaces] = useState([]);
    const [usage, setUsage] = useState(null);
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        Promise.all([
            fetchTenants().catch(() => []),
            fetchUsers().catch(() => []),
            fetchAdminWorkspaces().catch(() => []),
            fetchUsage().catch(() => null),
        ]).then(([t, u, w, us]) => {
            setTenants(t);
            setUsers(u);
            setWorkspaces(w);
            setUsage(us);
            setLoading(false);
        });
    }, []);
    if (loading)
        return _jsx("div", { className: "loading", children: "Loading admin dashboard" });
    const totalUsers = users.length;
    const totalWorkspaces = workspaces.length;
    const requestsToday = usage?.total_requests || 0;
    const activeSessions = users.filter((u) => u.last_login).length;
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Admin Dashboard" }), _jsx("p", { className: "page-subtitle", children: "Platform-wide overview and management" })] }), _jsxs("div", { className: "stat-grid", children: [_jsxs("div", { className: "stat-card stat-card-accent", children: [_jsx("div", { className: "stat-card-value", children: totalUsers }), _jsx("div", { className: "stat-card-label", children: "Total Users" })] }), _jsxs("div", { className: "stat-card stat-card-accent-success", children: [_jsx("div", { className: "stat-card-value", children: totalWorkspaces }), _jsx("div", { className: "stat-card-label", children: "Workspaces" })] }), _jsxs("div", { className: "stat-card stat-card-accent", children: [_jsx("div", { className: "stat-card-value", children: requestsToday }), _jsx("div", { className: "stat-card-label", children: "Requests Today" })] }), _jsxs("div", { className: "stat-card stat-card-accent-warning", children: [_jsx("div", { className: "stat-card-value", children: activeSessions }), _jsx("div", { className: "stat-card-label", children: "Active Sessions" })] })] }), tenants.length > 0 && (_jsxs("div", { className: "detail-section", children: [_jsx("h2", { className: "detail-section-title", children: "Tenants" }), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Name" }), _jsx("th", { children: "Domain" }), _jsx("th", { children: "Users" }), _jsx("th", { children: "Workspaces" })] }) }), _jsx("tbody", { children: tenants.map((t) => (_jsxs("tr", { children: [_jsx("td", { children: t.name }), _jsx("td", { style: { fontFamily: "var(--font-mono)", fontSize: "0.82rem" }, children: t.domain }), _jsx("td", { children: t.user_count }), _jsx("td", { children: t.workspace_count })] }, t.id))) })] }) })] })), _jsxs("div", { className: "admin-nav", children: [_jsx("a", { href: "/admin/users", className: "admin-nav-link", children: "\uD83D\uDC65 Manage Users" }), _jsx("a", { href: "/admin/workspaces", className: "admin-nav-link", children: "\uD83C\uDFE2 Manage Workspaces" }), _jsx("a", { href: "/admin/audit", className: "admin-nav-link", children: "\uD83D\uDCDD Audit Log" }), _jsx("a", { href: "/admin/usage", className: "admin-nav-link", children: "\uD83D\uDCC8 Usage" })] })] }));
}
