import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { getCurrentUser, clearToken, fetchPermissions } from "../api";
import WorkspaceSwitcher from "./WorkspaceSwitcher";
const NAV_ITEMS = [
    { path: "/sessions", label: "Sessions", icon: "🗂️" },
    { path: "/dashboard", label: "Dashboard", icon: "📊" },
    { path: "/requests", label: "Requests", icon: "📋" },
    { path: "/quota", label: "Quota", icon: "📦" },
    { path: "/agents", label: "Agents", icon: "🤖" },
    { path: "/api-keys", label: "API Keys", icon: "🔑" },
];
const ADMIN_ITEMS = [
    { path: "/admin", label: "Overview", icon: "🛡️" },
    { path: "/admin/users", label: "Users", icon: "👥" },
    { path: "/admin/workspaces", label: "Workspaces", icon: "🏢" },
    { path: "/admin/observability", label: "Observability", icon: "📡" },
    { path: "/admin/audit", label: "Audit Log", icon: "📝" },
    { path: "/admin/usage", label: "Usage", icon: "📈" },
];
export default function Layout({ children }) {
    const navigate = useNavigate();
    const location = useLocation();
    const [user, setUser] = useState(null);
    const [visibleTabs, setVisibleTabs] = useState(new Set());
    const [visibleAdminTabs, setVisibleAdminTabs] = useState(new Set());
    useEffect(() => {
        getCurrentUser()
            .then(setUser)
            .catch(() => { });
    }, []);
    // Fetch permissions from backend to determine tab visibility.
    useEffect(() => {
        fetchPermissions()
            .then((resp) => {
            const tabs = new Set();
            const adminTabs = new Set();
            for (const [path, required] of Object.entries(resp.frontend_tabs)) {
                if (required !== null) {
                    if (path.startsWith("/admin")) {
                        adminTabs.add(path);
                    }
                    else {
                        tabs.add(path);
                    }
                }
                else {
                    // null = always visible
                    tabs.add(path);
                }
            }
            setVisibleTabs(tabs);
            setVisibleAdminTabs(adminTabs);
        })
            .catch(() => { });
    }, []);
    function handleLogout() {
        clearToken();
        navigate("/login");
    }
    const initials = user?.name
        ? user.name.split(" ").map(n => n[0]).join("").toUpperCase().slice(0, 2)
        : user?.email?.slice(0, 2).toUpperCase() || "??";
    return (_jsxs("div", { className: "app-layout", children: [_jsxs("aside", { className: "sidebar", children: [_jsxs("div", { className: "sidebar-brand", children: [_jsx("div", { className: "sidebar-brand-icon", children: "A" }), _jsx("span", { className: "sidebar-brand-text", children: "Agent Platform" })] }), _jsx("div", { className: "sidebar-workspace", children: _jsx(WorkspaceSwitcher, {}) }), _jsxs("nav", { className: "sidebar-nav", children: [_jsxs("div", { children: [_jsx("div", { className: "sidebar-section-label", children: "Main" }), NAV_ITEMS.filter(item => visibleTabs.has(item.path)).map((item) => {
                                        const isActive = item.path === "/"
                                            ? location.pathname === "/"
                                            : location.pathname.startsWith(item.path);
                                        return (_jsxs(Link, { to: item.path, className: `sidebar-link${isActive ? " active" : ""}`, children: [_jsx("span", { className: "sidebar-link-icon", children: item.icon }), _jsx("span", { children: item.label })] }, item.path));
                                    })] }), visibleAdminTabs.size > 0 && (_jsxs("div", { children: [_jsx("div", { className: "sidebar-section-label", children: "Admin" }), ADMIN_ITEMS.filter(item => visibleAdminTabs.has(item.path)).map((item) => {
                                        const isActive = location.pathname.startsWith(item.path);
                                        return (_jsxs(Link, { to: item.path, className: `sidebar-link${isActive ? " active" : ""}`, children: [_jsx("span", { className: "sidebar-link-icon", children: item.icon }), _jsx("span", { children: item.label })] }, item.path));
                                    })] }))] }), _jsxs("div", { className: "sidebar-footer", children: [_jsxs("div", { className: "sidebar-user", children: [_jsx("div", { className: "sidebar-user-avatar", children: initials }), _jsxs("div", { className: "sidebar-user-info", children: [_jsx("div", { className: "sidebar-user-name", children: user?.name || "User" }), _jsx("div", { className: "sidebar-user-email", children: user?.email || "" })] })] }), _jsx("button", { className: "sidebar-logout", onClick: handleLogout, children: "Sign out" })] })] }), _jsx("div", { className: "main-area", children: _jsx("main", { className: "main-content", children: children }) })] }));
}
