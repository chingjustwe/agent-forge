import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from "react";
import { useNavigate, Link } from "react-router-dom";
import { getCurrentUser, clearToken } from "../api";
import WorkspaceSwitcher from "./WorkspaceSwitcher";
export default function Header() {
    const navigate = useNavigate();
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
    return (_jsxs("header", { style: {
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 16px",
            borderBottom: "1px solid #ccc",
            background: "#f8f8f8",
        }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 16 }, children: [_jsx(Link, { to: "/", style: { textDecoration: "none", color: "inherit", fontWeight: "bold" }, children: "Agent Platform" }), _jsx(WorkspaceSwitcher, {})] }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: 12 }, children: [_jsx(Link, { to: "/dashboard", style: { padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }, children: "Dashboard" }), _jsx(Link, { to: "/requests", style: { padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }, children: "Requests" }), _jsx(Link, { to: "/quota", style: { padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }, children: "Quota" }), _jsx(Link, { to: "/settings", style: { padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }, children: "Settings" }), user?.role === "tenant_admin" && (_jsx(Link, { to: "/admin", style: { padding: "4px 12px", background: "#eee", borderRadius: 4, textDecoration: "none", color: "inherit", fontSize: "0.9em" }, children: "Admin" })), _jsx("span", { style: { fontSize: "0.9em", color: "#666" }, children: user?.email || "" }), _jsx("button", { onClick: handleLogout, style: { padding: "4px 12px", cursor: "pointer" }, children: "Logout" })] })] }));
}
