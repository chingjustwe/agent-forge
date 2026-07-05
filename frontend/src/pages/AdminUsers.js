import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { fetchUsers, updateUser, deleteUser, inviteUser, fetchAdminWorkspaces, listPendingInvitations, deletePendingInvitation, } from "../api";
function roleLabel(role) {
    switch (role) {
        case "tenant_admin": return "Tenant Admin";
        case "workspace_admin": return "Workspace Admin";
        case "member": return "Member";
        case "viewer": return "Viewer";
        default: return role;
    }
}
function countdown(expiresAt) {
    const diff = new Date(expiresAt).getTime() - Date.now();
    if (diff <= 0)
        return "Expired";
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor((diff % 86400000) / 3600000);
    if (days > 0)
        return `${days}d ${hours}h`;
    return `${hours}h ${Math.floor((diff % 3600000) / 60000)}m`;
}
export default function AdminUsers() {
    const [users, setUsers] = useState([]);
    const [pending, setPending] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [roleFilter, setRoleFilter] = useState("");
    const [editingId, setEditingId] = useState(null);
    const [editRole, setEditRole] = useState("");
    const [showInvite, setShowInvite] = useState(false);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState("member");
    const [message, setMessage] = useState("");
    const [workspaces, setWorkspaces] = useState([]);
    const [inviteWsId, setInviteWsId] = useState("");
    const [inviteExpires, setInviteExpires] = useState(7);
    const load = () => {
        setLoading(true);
        Promise.all([
            fetchUsers({ search: search || undefined, role: roleFilter || undefined }),
            listPendingInvitations().catch(() => []),
        ])
            .then(([u, p]) => { setUsers(u); setPending(p); })
            .finally(() => setLoading(false));
    };
    useEffect(() => {
        load();
        fetchAdminWorkspaces().then(setWorkspaces).catch(() => { });
    }, []);
    const handleSearch = () => load();
    const handleDelete = async (id) => {
        if (!confirm("Delete this user?"))
            return;
        try {
            await deleteUser(id);
            setUsers(users.filter((u) => u.id !== id));
        }
        catch (e) {
            setMessage(String(e));
        }
    };
    const handleDeletePending = async (userId, email) => {
        if (!confirm(`Cancel invitation for ${email}?`))
            return;
        try {
            await deletePendingInvitation(userId);
            setPending(pending.filter((p) => p.user_id !== userId));
            setMessage(`Invitation cancelled for ${email}`);
        }
        catch (e) {
            setMessage(String(e));
        }
    };
    const handleSaveRole = async (id) => {
        try {
            await updateUser(id, { role: editRole });
            setEditingId(null);
            load();
        }
        catch (e) {
            setMessage(String(e));
        }
    };
    const handleInvite = async () => {
        try {
            await inviteUser({ email: inviteEmail, role: inviteRole, workspace_id: inviteWsId || undefined, expires_in_days: inviteExpires });
            setShowInvite(false);
            setInviteEmail("");
            setInviteWsId("");
            setInviteExpires(7);
            setMessage(`Invitation sent to ${inviteEmail}`);
            load();
        }
        catch (e) {
            setMessage(String(e));
        }
    };
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "User Management" }), _jsx("p", { className: "page-subtitle", children: "Manage users, roles, and invitations" })] }), message && _jsx("div", { className: "alert alert-error", children: message }), _jsxs("div", { className: "filter-bar", children: [_jsx("input", { placeholder: "Search email or name...", value: search, onChange: (e) => setSearch(e.target.value), style: { minWidth: 200 } }), _jsxs("select", { value: roleFilter, onChange: (e) => setRoleFilter(e.target.value), children: [_jsx("option", { value: "", children: "All roles" }), _jsx("option", { value: "tenant_admin", children: "Tenant Admin" }), _jsx("option", { value: "member", children: "Member" })] }), _jsx("button", { className: "btn btn-secondary", onClick: handleSearch, children: "Search" }), _jsx("button", { className: "btn btn-success", onClick: () => setShowInvite(true), children: "Invite User" })] }), showInvite && (_jsxs("div", { className: "card", style: { marginBottom: 20, display: "flex", gap: 10, alignItems: "flex-end" }, children: [_jsxs("div", { className: "form-group", style: { margin: 0, flex: 1 }, children: [_jsx("label", { className: "form-label", children: "Email" }), _jsx("input", { placeholder: "Email", value: inviteEmail, onChange: (e) => setInviteEmail(e.target.value) })] }), _jsxs("div", { className: "form-group", style: { margin: 0 }, children: [_jsx("label", { className: "form-label", children: "Role" }), _jsxs("select", { value: inviteRole, onChange: (e) => setInviteRole(e.target.value), children: [_jsx("option", { value: "member", children: "Member" }), _jsx("option", { value: "workspace_admin", children: "Workspace Admin" }), _jsx("option", { value: "tenant_admin", children: "Tenant Admin" })] })] }), _jsxs("div", { className: "form-group", style: { margin: 0 }, children: [_jsx("label", { className: "form-label", children: "Workspace" }), _jsxs("select", { value: inviteWsId, onChange: (e) => setInviteWsId(e.target.value), children: [_jsx("option", { value: "", children: "(Default)" }), workspaces.map((ws) => (_jsx("option", { value: ws.id, children: ws.name }, ws.id)))] })] }), _jsxs("div", { className: "form-group", style: { margin: 0 }, children: [_jsx("label", { className: "form-label", children: "Expires" }), _jsxs("select", { value: inviteExpires, onChange: (e) => setInviteExpires(Number(e.target.value)), children: [_jsx("option", { value: 1, children: "1 day" }), _jsx("option", { value: 3, children: "3 days" }), _jsx("option", { value: 7, children: "7 days" }), _jsx("option", { value: 14, children: "14 days" }), _jsx("option", { value: 30, children: "30 days" })] })] }), _jsxs("div", { className: "btn-group", children: [_jsx("button", { className: "btn btn-primary", onClick: handleInvite, children: "Send Invite" }), _jsx("button", { className: "btn btn-secondary", onClick: () => setShowInvite(false), children: "Cancel" })] })] })), loading ? (_jsx("div", { className: "loading", children: "Loading users" })) : (_jsxs(_Fragment, { children: [_jsxs("h2", { style: { fontSize: "1rem", marginBottom: 8, color: "var(--text-secondary)" }, children: ["Active Users (", users.length, ")"] }), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Email" }), _jsx("th", { children: "Name" }), _jsx("th", { children: "Role" }), _jsx("th", { children: "Workspaces" }), _jsx("th", { children: "Created" }), _jsx("th", { children: "Actions" })] }) }), _jsx("tbody", { children: users.length === 0 ? (_jsx("tr", { children: _jsx("td", { colSpan: 6, style: { textAlign: "center", color: "var(--text-muted)" }, children: "No active users" }) })) : users.map((u) => (_jsxs("tr", { children: [_jsx("td", { children: u.email }), _jsx("td", { children: u.name }), _jsx("td", { children: editingId === u.id ? (_jsxs("select", { value: editRole, onChange: (e) => setEditRole(e.target.value), children: [_jsx("option", { value: "member", children: "Member" }), _jsx("option", { value: "workspace_admin", children: "Workspace Admin" }), _jsx("option", { value: "tenant_admin", children: "Tenant Admin" })] })) : (_jsx("span", { className: "badge badge-primary", children: roleLabel(u.role) })) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: Array.isArray(u.workspaces) ? u.workspaces.join(", ") : u.workspaces }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: new Date(u.created_at).toLocaleDateString() }), _jsx("td", { children: _jsxs("div", { className: "btn-group", children: [editingId === u.id ? (_jsx("button", { className: "btn btn-primary btn-sm", onClick: () => handleSaveRole(u.id), children: "Save" })) : (_jsx("button", { className: "btn btn-secondary btn-sm", onClick: () => { setEditingId(u.id); setEditRole(u.role); }, children: "Edit" })), _jsx("button", { className: "btn btn-danger btn-sm", onClick: () => handleDelete(u.id), children: "Delete" })] }) })] }, u.id))) })] }) }), pending.length > 0 && (_jsxs(_Fragment, { children: [_jsxs("h2", { style: { fontSize: "1rem", marginTop: 28, marginBottom: 8, color: "var(--text-secondary)" }, children: ["Pending Invitations (", pending.length, ")"] }), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Email" }), _jsx("th", { children: "Role" }), _jsx("th", { children: "Workspace" }), _jsx("th", { children: "Invited" }), _jsx("th", { children: "Expires" }), _jsx("th", { children: "Actions" })] }) }), _jsx("tbody", { children: pending.map((p) => (_jsxs("tr", { children: [_jsx("td", { children: p.email }), _jsx("td", { children: _jsx("span", { className: "badge badge-secondary", children: roleLabel(p.invited_role || p.role) }) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: p.workspace_name || "—" }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: p.invited_at ? new Date(p.invited_at).toLocaleDateString() : "—" }), _jsx("td", { children: _jsx("span", { style: {
                                                                fontSize: "0.82rem",
                                                                color: new Date(p.expires_at).getTime() < Date.now() ? "var(--danger)" : "var(--text-secondary)"
                                                            }, children: p.expires_at ? countdown(p.expires_at) : "—" }) }), _jsx("td", { children: _jsx("button", { className: "btn btn-danger btn-sm", onClick: () => handleDeletePending(p.user_id, p.email), children: "Cancel" }) })] }, p.user_id))) })] }) })] }))] }))] }));
}
