import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useWorkspace } from "../context/WorkspaceContext";
export default function WorkspaceSwitcher() {
    const { workspaces, currentWorkspaceId, switchTo, loading } = useWorkspace();
    // 未登录或无 workspace 时完全不渲染（保持 sidebar 简洁）
    if (loading) {
        return (_jsxs("div", { className: "ws-switcher-block", children: [_jsxs("div", { className: "ws-switcher-label", children: [_jsx("span", { className: "ws-switcher-label-icon", "aria-hidden": true, children: "\uD83C\uDFE2" }), _jsx("span", { children: "Workspace" })] }), _jsx("span", { className: "ws-switcher-loading", children: "Loading\u2026" })] }));
    }
    if (workspaces.length === 0)
        return null;
    const onlyOne = workspaces.length === 1;
    return (_jsxs("div", { className: "ws-switcher-block", children: [_jsxs("div", { className: "ws-switcher-label", children: [_jsx("span", { className: "ws-switcher-label-icon", "aria-hidden": true, children: "\uD83C\uDFE2" }), _jsx("span", { children: "Workspace" }), _jsx("span", { className: "ws-switcher-count", children: workspaces.length })] }), _jsxs("div", { className: "ws-switcher-select-wrap", children: [_jsx("select", { className: "ws-switcher", value: currentWorkspaceId, onChange: (e) => switchTo(e.target.value), disabled: onlyOne, title: onlyOne ? "You are in the only workspace" : "Switch workspace", children: workspaces.map(w => (_jsxs("option", { value: w.id, children: [w.name, w.role === "workspace_owner" ? " · Owner" : w.role === "workspace_admin" ? " · Admin" : ""] }, w.id))) }), _jsx("span", { className: "ws-switcher-chevron", "aria-hidden": true, children: "\u25BE" })] }), onlyOne && (_jsx("div", { className: "ws-switcher-hint", children: "Only workspace \u2014 invite members to create more" }))] }));
}
