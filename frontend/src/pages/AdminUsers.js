import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { fetchUsers, updateUser, deleteUser, inviteUser, fetchAdminWorkspaces } from "../api";
export default function AdminUsers() {
    const [users, setUsers] = useState([]);
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
    const load = () => {
        setLoading(true);
        fetchUsers({ search: search || undefined, role: roleFilter || undefined })
            .then(setUsers)
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
            await inviteUser({ email: inviteEmail, role: inviteRole, workspace_id: inviteWsId || undefined });
            setShowInvite(false);
            setInviteEmail("");
            setInviteWsId("");
            setMessage(`Invitation sent to ${inviteEmail}`);
            load();
        }
        catch (e) {
            setMessage(String(e));
        }
    };
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "User Management" }), _jsx("p", { className: "page-subtitle", children: "Manage users, roles, and invitations" })] }), message && _jsx("div", { className: "alert alert-error", children: message }), _jsxs("div", { className: "filter-bar", children: [_jsx("input", { placeholder: "Search email or name...", value: search, onChange: (e) => setSearch(e.target.value), style: { minWidth: 200 } }), _jsxs("select", { value: roleFilter, onChange: (e) => setRoleFilter(e.target.value), children: [_jsx("option", { value: "", children: "All roles" }), _jsx("option", { value: "tenant_admin", children: "Tenant Admin" }), _jsx("option", { value: "workspace_owner", children: "Workspace Owner" }), _jsx("option", { value: "workspace_admin", children: "Workspace Admin" }), _jsx("option", { value: "member", children: "Member" }), _jsx("option", { value: "viewer", children: "Viewer" })] }), _jsx("button", { className: "btn btn-secondary", onClick: handleSearch, children: "Search" }), _jsx("button", { className: "btn btn-success", onClick: () => setShowInvite(true), children: "Invite User" })] }), showInvite && (_jsxs("div", { className: "card", style: { marginBottom: 20, display: "flex", gap: 10, alignItems: "flex-end" }, children: [_jsxs("div", { className: "form-group", style: { margin: 0, flex: 1 }, children: [_jsx("label", { className: "form-label", children: "Email" }), _jsx("input", { placeholder: "Email", value: inviteEmail, onChange: (e) => setInviteEmail(e.target.value) })] }), _jsxs("div", { className: "form-group", style: { margin: 0 }, children: [_jsx("label", { className: "form-label", children: "Role" }), _jsxs("select", { value: inviteRole, onChange: (e) => setInviteRole(e.target.value), children: [_jsx("option", { value: "member", children: "Member" }), _jsx("option", { value: "tenant_admin", children: "Tenant Admin" })] })] }), _jsxs("div", { className: "form-group", style: { margin: 0 }, children: [_jsx("label", { className: "form-label", children: "Workspace" }), _jsxs("select", { value: inviteWsId, onChange: (e) => setInviteWsId(e.target.value), children: [_jsx("option", { value: "", children: "(Default)" }), workspaces.map((ws) => (_jsx("option", { value: ws.id, children: ws.name }, ws.id)))] })] }), _jsxs("div", { className: "btn-group", children: [_jsx("button", { className: "btn btn-primary", onClick: handleInvite, children: "Send Invite" }), _jsx("button", { className: "btn btn-secondary", onClick: () => setShowInvite(false), children: "Cancel" })] })] })), loading ? (_jsx("div", { className: "loading", children: "Loading users" })) : (_jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Email" }), _jsx("th", { children: "Name" }), _jsx("th", { children: "Role" }), _jsx("th", { children: "Workspaces" }), _jsx("th", { children: "Created" }), _jsx("th", { children: "Actions" })] }) }), _jsx("tbody", { children: users.map((u) => (_jsxs("tr", { children: [_jsx("td", { children: u.email }), _jsx("td", { children: u.name }), _jsx("td", { children: editingId === u.id ? (_jsxs("select", { value: editRole, onChange: (e) => setEditRole(e.target.value), children: [_jsx("option", { value: "tenant_admin", children: "Tenant Admin" }), _jsx("option", { value: "member", children: "Member" })] })) : (_jsx("span", { className: "badge badge-primary", children: u.role })) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: Array.isArray(u.workspaces) ? u.workspaces.join(", ") : u.workspaces }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: new Date(u.created_at).toLocaleDateString() }), _jsx("td", { children: _jsxs("div", { className: "btn-group", children: [editingId === u.id ? (_jsx("button", { className: "btn btn-primary btn-sm", onClick: () => handleSaveRole(u.id), children: "Save" })) : (_jsx("button", { className: "btn btn-secondary btn-sm", onClick: () => { setEditingId(u.id); setEditRole(u.role); }, children: "Edit" })), _jsx("button", { className: "btn btn-danger btn-sm", onClick: () => handleDelete(u.id), children: "Delete" })] }) })] }, u.id))) })] }) }))] }));
}
