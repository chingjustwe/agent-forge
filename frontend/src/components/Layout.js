import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { getCurrentUser, clearToken } from "../api";
import WorkspaceSwitcher from "./WorkspaceSwitcher";
const NAV_ITEMS = [
    { section: "Main", items: [
            { path: "/sessions", label: "Sessions", icon: "🗂️" },
            { path: "/dashboard", label: "Dashboard", icon: "📊" },
            { path: "/requests", label: "Requests", icon: "📋" },
            { path: "/quota", label: "Quota", icon: "📦" },
            { path: "/agents", label: "Agents", icon: "🤖" },
            { path: "/invitations", label: "Invitations", icon: "✉️" },
            { path: "/api-keys", label: "API Keys", icon: "🔑" },
            { path: "/settings", label: "Settings", icon: "⚙️" },
        ] },
];
export default function Layout({ children }) {
    const navigate = useNavigate();
    const location = useLocation();
    const [user, setUser] = useState(null);
    useEffect(() => {
        getCurrentUser()
            .then(setUser)
            .catch(() => { });
    }, []);
    function handleLogout() {
        clearToken();
        navigate("/login");
    }
    // Sidebar Admin entry: tenant-level admin only.
    // Workspace-level admin entries (if any) are handled inside business pages.
    const isAdmin = user?.role === "tenant_admin";
    const initials = user?.name
        ? user.name.split(" ").map(n => n[0]).join("").toUpperCase().slice(0, 2)
        : user?.email?.slice(0, 2).toUpperCase() || "??";
    return (_jsxs("div", { className: "app-layout", children: [_jsxs("aside", { className: "sidebar", children: [_jsxs("div", { className: "sidebar-brand", children: [_jsx("div", { className: "sidebar-brand-icon", children: "A" }), _jsx("span", { className: "sidebar-brand-text", children: "Agent Platform" })] }), _jsx("div", { className: "sidebar-workspace", children: _jsx(WorkspaceSwitcher, {}) }), _jsxs("nav", { className: "sidebar-nav", children: [NAV_ITEMS.map((group) => (_jsxs("div", { children: [_jsx("div", { className: "sidebar-section-label", children: group.section }), group.items.map((item) => {
                                        const isActive = item.path === "/"
                                            ? location.pathname === "/"
                                            : location.pathname.startsWith(item.path);
                                        return (_jsxs(Link, { to: item.path, className: `sidebar-link${isActive ? " active" : ""}`, children: [_jsx("span", { className: "sidebar-link-icon", children: item.icon }), _jsx("span", { children: item.label })] }, item.path));
                                    })] }, group.section))), isAdmin && (_jsxs("div", { children: [_jsx("div", { className: "sidebar-section-label", children: "Admin" }), [
                                        { path: "/admin", label: "Overview", icon: "🛡️" },
                                        { path: "/admin/users", label: "Users", icon: "👥" },
                                        { path: "/admin/workspaces", label: "Workspaces", icon: "🏢" },
                                        { path: "/admin/audit", label: "Audit Log", icon: "📝" },
                                        { path: "/admin/usage", label: "Usage", icon: "📈" },
                                    ].map((item) => {
                                        const isActive = location.pathname.startsWith(item.path);
                                        return (_jsxs(Link, { to: item.path, className: `sidebar-link${isActive ? " active" : ""}`, children: [_jsx("span", { className: "sidebar-link-icon", children: item.icon }), _jsx("span", { children: item.label })] }, item.path));
                                    })] }))] }), _jsxs("div", { className: "sidebar-footer", children: [_jsxs("div", { className: "sidebar-user", children: [_jsx("div", { className: "sidebar-user-avatar", children: initials }), _jsxs("div", { className: "sidebar-user-info", children: [_jsx("div", { className: "sidebar-user-name", children: user?.name || "User" }), _jsx("div", { className: "sidebar-user-email", children: user?.email || "" })] })] }), _jsx("button", { className: "sidebar-logout", onClick: handleLogout, children: "Sign out" })] })] }), _jsx("div", { className: "main-area", children: _jsx("main", { className: "main-content", children: children }) })] }));
}
