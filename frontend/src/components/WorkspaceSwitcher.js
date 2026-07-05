import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useRef, useEffect } from "react";
import { useWorkspace } from "../context/WorkspaceContext";
export default function WorkspaceSwitcher() {
    const { workspaces, currentWorkspaceId, switchTo, loading } = useWorkspace();
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    // Close dropdown on outside click
    useEffect(() => {
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) {
                setOpen(false);
            }
        };
        document.addEventListener("mousedown", handler);
        return () => document.removeEventListener("mousedown", handler);
    }, []);
    if (loading) {
        return (_jsxs("div", { className: "ws-switcher-block", children: [_jsxs("div", { className: "ws-switcher-label", children: [_jsx("span", { className: "ws-switcher-label-icon", "aria-hidden": true, children: "\uD83C\uDFE2" }), _jsx("span", { children: "Workspace" })] }), _jsx("span", { className: "ws-switcher-loading", children: "Loading\u2026" })] }));
    }
    if (workspaces.length === 0)
        return null;
    const current = workspaces.find(w => w.id === currentWorkspaceId);
    const onlyOne = workspaces.length === 1;
    function renderIcon(icon) {
        if (!icon)
            return null;
        if (/^https?:\/\//.test(icon)) {
            return _jsx("img", { src: icon, alt: "", style: { width: 16, height: 16, objectFit: "contain", flexShrink: 0 } });
        }
        return _jsx("span", { style: { fontSize: "1rem", flexShrink: 0 }, children: icon });
    }
    return (_jsxs("div", { className: "ws-switcher-block", ref: ref, children: [_jsxs("div", { className: "ws-switcher-label", children: [_jsx("span", { className: "ws-switcher-label-icon", "aria-hidden": true, children: "\uD83C\uDFE2" }), _jsx("span", { children: "Workspace" }), _jsx("span", { className: "ws-switcher-count", children: workspaces.length })] }), _jsxs("div", { className: "ws-switcher-select-wrap", onClick: () => !onlyOne && setOpen(!open), style: { cursor: onlyOne ? "default" : "pointer" }, title: onlyOne ? "You are in the only workspace" : "Switch workspace", children: [_jsxs("div", { className: "ws-switcher-trigger", children: [renderIcon(current?.icon), _jsx("span", { style: { flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, children: current?.name || "" }), current?.role === "workspace_admin" && (_jsx("span", { style: { color: "var(--text-muted)", fontSize: "0.75rem", flexShrink: 0 }, children: "Admin" })), !onlyOne && _jsx("span", { className: "ws-switcher-chevron", "aria-hidden": true, children: "\u25BE" })] }), open && !onlyOne && (_jsx("div", { className: "ws-switcher-dropdown", children: workspaces.map(w => (_jsxs("div", { className: `ws-switcher-option${w.id === currentWorkspaceId ? " active" : ""}`, onClick: (e) => { e.stopPropagation(); switchTo(w.id); setOpen(false); }, children: [renderIcon(w.icon), _jsx("span", { style: { flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, children: w.name }), w.role === "workspace_admin" && (_jsx("span", { style: { color: "var(--text-muted)", fontSize: "0.75rem", flexShrink: 0 }, children: "Admin" }))] }, w.id))) }))] }), onlyOne && (_jsx("div", { className: "ws-switcher-hint", children: "Only workspace \u2014 invite members to create more" }))] }));
}
