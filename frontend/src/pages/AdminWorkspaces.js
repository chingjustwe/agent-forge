import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState, useRef } from "react";
import { fetchAdminWorkspaces, createAdminWorkspace, updateAdminWorkspace, archiveWorkspace, addWorkspaceMember, removeWorkspaceMember, fetchWorkspaceMembers, fetchUsers } from "../api";
export default function AdminWorkspaces() {
    const [workspaces, setWorkspaces] = useState([]);
    const [loading, setLoading] = useState(true);
    const [editingId, setEditingId] = useState(null);
    const [editName, setEditName] = useState("");
    const [editSlug, setEditSlug] = useState("");
    const [editDescription, setEditDescription] = useState("");
    const [editIcon, setEditIcon] = useState("");
    const [editTokens, setEditTokens] = useState(0);
    const [editCost, setEditCost] = useState(0);
    const [message, setMessage] = useState("");
    const [messageType, setMessageType] = useState("error");
    const [creating, setCreating] = useState(false);
    const [newWsName, setNewWsName] = useState("");
    const [newWsSlug, setNewWsSlug] = useState("");
    const [newWsDescription, setNewWsDescription] = useState("");
    const [newWsIcon, setNewWsIcon] = useState("");
    // Member management state
    const [memberWsId, setMemberWsId] = useState(null);
    const [members, setMembers] = useState([]);
    const [memberLoading, setMemberLoading] = useState(false);
    const [addRole, setAddRole] = useState("member");
    const [searchQuery, setSearchQuery] = useState("");
    const [searchResults, setSearchResults] = useState([]);
    const [searching, setSearching] = useState(false);
    const [addingUser, setAddingUser] = useState(null);
    const searchTimer = useRef(null);
    const [showDropdown, setShowDropdown] = useState(false);
    const showMessage = (msg, type = "error") => {
        setMessage(msg);
        setMessageType(type);
    };
    const load = () => {
        setLoading(true);
        fetchAdminWorkspaces()
            .then(setWorkspaces)
            .finally(() => setLoading(false));
    };
    useEffect(() => { load(); }, []);
    const handleCreate = async (e) => {
        e.preventDefault();
        if (!newWsName.trim())
            return;
        try {
            await createAdminWorkspace(newWsName.trim(), {
                slug: newWsSlug.trim() || undefined,
                description: newWsDescription.trim() || undefined,
                icon: newWsIcon.trim() || undefined,
            });
            setNewWsName("");
            setNewWsSlug("");
            setNewWsDescription("");
            setNewWsIcon("");
            setCreating(false);
            showMessage("Workspace created", "success");
            load();
        }
        catch (e) {
            showMessage(e instanceof Error ? e.message : "Failed to create workspace");
        }
    };
    const handleSave = async (id) => {
        try {
            await updateAdminWorkspace(id, {
                name: editName,
                slug: editSlug,
                description: editDescription,
                icon: editIcon,
                max_tokens_per_day: editTokens,
                max_cost_per_month: editCost,
            });
            setEditingId(null);
            load();
        }
        catch (e) {
            showMessage(String(e));
        }
    };
    const handleArchive = async (id, name) => {
        if (!confirm(`Archive workspace "${name}"?`))
            return;
        try {
            await archiveWorkspace(id);
            showMessage("Workspace archived", "success");
            load();
        }
        catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            showMessage(msg);
        }
    };
    const openMembers = async (wsId) => {
        setMemberWsId(wsId);
        setMemberLoading(true);
        setSearchQuery("");
        setSearchResults([]);
        setShowDropdown(false);
        setAddRole("member");
        try {
            const data = await fetchWorkspaceMembers(wsId);
            setMembers(data);
        }
        catch (e) {
            showMessage(e instanceof Error ? e.message : "Failed to load members");
        }
        finally {
            setMemberLoading(false);
        }
    };
    const handleAddMember = async (userId, userName) => {
        if (!memberWsId)
            return;
        setAddingUser(userId);
        try {
            await addWorkspaceMember(memberWsId, userId, addRole);
            showMessage(`${userName} added`, "success");
            setSearchQuery("");
            setSearchResults([]);
            setShowDropdown(false);
            await openMembers(memberWsId);
            load(); // refresh to update member_count
        }
        catch (e) {
            showMessage(e instanceof Error ? e.message : "Failed to add member");
        }
        finally {
            setAddingUser(null);
        }
    };
    const handleSearch = (value) => {
        setSearchQuery(value);
        if (searchTimer.current)
            clearTimeout(searchTimer.current);
        if (!value.trim()) {
            setSearchResults([]);
            setShowDropdown(false);
            return;
        }
        searchTimer.current = setTimeout(async () => {
            setSearching(true);
            try {
                const users = await fetchUsers({ search: value.trim() });
                // Filter out users already in this workspace
                const memberIds = new Set(members.map(m => m.user_id));
                setSearchResults(users.filter(u => !memberIds.has(u.id)));
                setShowDropdown(true);
            }
            catch {
                setSearchResults([]);
            }
            finally {
                setSearching(false);
            }
        }, 300);
    };
    const handleRemoveMember = async (userId, email) => {
        if (!memberWsId || !confirm(`Remove ${email} from this workspace?`))
            return;
        try {
            await removeWorkspaceMember(memberWsId, userId);
            showMessage("Member removed", "success");
            await openMembers(memberWsId);
            load(); // refresh to update member_count
        }
        catch (e) {
            showMessage(e instanceof Error ? e.message : "Failed to remove member");
        }
    };
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Workspace Management" }), _jsx("p", { className: "page-subtitle", children: "Manage workspaces, quotas, and settings" })] }), message && _jsx("div", { className: `alert alert-${messageType}`, children: message }), _jsxs("div", { className: "card", style: { marginBottom: 20 }, children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: "Create Workspace" }) }), creating ? (_jsxs("form", { onSubmit: handleCreate, style: { display: "flex", flexDirection: "column", gap: 8 }, children: [_jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { value: newWsName, onChange: (e) => setNewWsName(e.target.value), placeholder: "Workspace name *", style: { flex: 1 }, autoFocus: true }), _jsx("input", { value: newWsIcon, onChange: (e) => setNewWsIcon(e.target.value), placeholder: "Icon (emoji or URL)", style: { width: 180 } })] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { value: newWsSlug, onChange: (e) => setNewWsSlug(e.target.value), placeholder: "Slug (optional, auto-generated from name)", style: { flex: 1 } }), _jsx("input", { value: newWsDescription, onChange: (e) => setNewWsDescription(e.target.value), placeholder: "Description (optional)", style: { flex: 1 } })] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("button", { type: "submit", className: "btn btn-primary", children: "Create" }), _jsx("button", { type: "button", className: "btn btn-secondary", onClick: () => { setCreating(false); setNewWsName(""); setNewWsSlug(""); setNewWsDescription(""); setNewWsIcon(""); }, children: "Cancel" })] })] })) : (_jsx("button", { className: "btn btn-primary", onClick: () => setCreating(true), children: "+ New Workspace" }))] }), loading ? (_jsx("div", { className: "loading", children: "Loading workspaces" })) : (_jsxs(_Fragment, { children: [_jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { style: { width: 40 }, children: "Icon" }), _jsx("th", { children: "Name" }), _jsx("th", { children: "Slug" }), _jsx("th", { children: "Description" }), _jsx("th", { children: "Members" }), _jsx("th", { children: "Agents" }), _jsx("th", { children: "Owner" }), _jsx("th", { children: "Created" }), _jsx("th", { children: "Actions" })] }) }), _jsx("tbody", { children: workspaces.map((ws) => (_jsxs("tr", { children: [_jsx("td", { style: { width: 32, maxWidth: 32, overflow: "hidden", textAlign: "center" }, children: ws.icon ? (/^https?:\/\//.test(ws.icon) ? (_jsx("img", { src: ws.icon, alt: "", style: { width: 20, height: 20, objectFit: "contain", verticalAlign: "middle" } })) : (_jsx("span", { style: { fontSize: "1.1rem" }, children: ws.icon }))) : null }), _jsx("td", { children: editingId === ws.id ? (_jsx("input", { value: editName, onChange: (e) => setEditName(e.target.value) })) : (ws.name) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: editingId === ws.id ? (_jsx("input", { value: editSlug, onChange: (e) => setEditSlug(e.target.value), placeholder: "slug", style: { width: 120 } })) : (ws.slug || "") }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: editingId === ws.id ? (_jsx("input", { value: editDescription, onChange: (e) => setEditDescription(e.target.value), placeholder: "description", style: { width: 160 } })) : (ws.description || "") }), _jsx("td", { children: ws.member_count }), _jsx("td", { children: ws.agent_count }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: ws.owner }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: new Date(ws.created_at).toLocaleDateString() }), _jsx("td", { children: editingId === ws.id ? (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 6 }, children: [_jsxs("div", { children: [_jsx("label", { style: { fontSize: "0.75rem", color: "var(--text-muted)", marginRight: 4 }, children: "Icon:" }), _jsx("input", { value: editIcon, onChange: (e) => setEditIcon(e.target.value), placeholder: "emoji / URL", style: { width: 120 } })] }), _jsxs("div", { children: [_jsx("label", { style: { fontSize: "0.75rem", color: "var(--text-muted)", marginRight: 4 }, children: "Tokens/day:" }), _jsx("input", { type: "number", value: editTokens, onChange: (e) => setEditTokens(Number(e.target.value)), style: { width: 80 } })] }), _jsxs("div", { children: [_jsx("label", { style: { fontSize: "0.75rem", color: "var(--text-muted)", marginRight: 4 }, children: "Cost/month:" }), _jsx("input", { type: "number", value: editCost, onChange: (e) => setEditCost(Number(e.target.value)), style: { width: 80 } })] }), _jsxs("div", { className: "btn-group", children: [_jsx("button", { className: "btn btn-primary btn-sm", onClick: () => handleSave(ws.id), children: "Save" }), _jsx("button", { className: "btn btn-secondary btn-sm", onClick: () => setEditingId(null), children: "Cancel" })] })] })) : (_jsxs("div", { className: "btn-group", children: [_jsx("button", { className: "btn btn-secondary btn-sm", onClick: () => { setEditingId(ws.id); setEditName(ws.name); setEditSlug(ws.slug || ""); setEditDescription(ws.description || ""); setEditIcon(ws.icon || ""); setEditTokens(0); setEditCost(0); }, children: "Edit" }), _jsx("button", { className: "btn btn-secondary btn-sm", onClick: () => openMembers(ws.id), children: "Members" }), !ws.is_default && (_jsx("button", { className: "btn btn-danger btn-sm", onClick: () => handleArchive(ws.id, ws.name), children: "Archive" }))] })) })] }, ws.id))) })] }) }), memberWsId && (_jsxs("div", { className: "card", style: { marginTop: 20 }, children: [_jsx("div", { className: "card-header", children: _jsxs("h3", { className: "card-title", children: ["Members of ", workspaces.find(w => w.id === memberWsId)?.name || memberWsId] }) }), memberLoading ? (_jsx("div", { className: "loading", style: { padding: 12 }, children: "Loading members..." })) : (_jsxs(_Fragment, { children: [members.length === 0 ? (_jsx("p", { style: { padding: 12, color: "var(--text-muted)" }, children: "No members yet." })) : (_jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Email" }), _jsx("th", { children: "Name" }), _jsx("th", { children: "Role" }), _jsx("th", { children: "Actions" })] }) }), _jsx("tbody", { children: members.map((m) => (_jsxs("tr", { children: [_jsx("td", { children: m.email }), _jsx("td", { children: m.name }), _jsx("td", { children: _jsx("span", { className: "badge badge-primary", children: m.role }) }), _jsx("td", { children: _jsx("button", { className: "btn btn-danger btn-sm", onClick: () => handleRemoveMember(m.user_id, m.email), children: "Remove" }) })] }, m.user_id))) })] })), _jsx("div", { style: { padding: 12, borderTop: "1px solid var(--border)" }, children: _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "flex-end" }, children: [_jsxs("div", { className: "form-group", style: { flex: 1, margin: 0, position: "relative" }, children: [_jsx("label", { className: "form-label", children: "Search registered users" }), _jsx("input", { value: searchQuery, onChange: e => handleSearch(e.target.value), placeholder: "Search by name or email...", onFocus: () => searchResults.length > 0 && setShowDropdown(true), onBlur: () => setTimeout(() => setShowDropdown(false), 200) }), showDropdown && (_jsx("div", { style: {
                                                                position: "absolute", top: "100%", left: 0, right: 0,
                                                                background: "var(--bg)", border: "1px solid var(--border)",
                                                                borderRadius: 6, maxHeight: 200, overflowY: "auto",
                                                                zIndex: 100, boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                                                            }, children: searching ? (_jsx("div", { style: { padding: 8, color: "var(--text-muted)", fontSize: "0.85rem" }, children: "Searching..." })) : searchResults.length === 0 ? (_jsx("div", { style: { padding: 8, color: "var(--text-muted)", fontSize: "0.85rem" }, children: searchQuery ? "No matching users found" : "Start typing to search" })) : (searchResults.map(u => (_jsxs("div", { style: {
                                                                    display: "flex", alignItems: "center", gap: 8,
                                                                    padding: "6px 8px", cursor: "pointer",
                                                                    borderBottom: "1px solid var(--border)",
                                                                }, onMouseDown: () => handleAddMember(u.id, u.name || u.email), children: [_jsxs("div", { style: { flex: 1 }, children: [_jsx("div", { style: { fontSize: "0.9rem" }, children: u.name || u.email }), _jsx("div", { style: { fontSize: "0.75rem", color: "var(--text-muted)" }, children: u.email })] }), _jsx("span", { className: "badge badge-primary", style: { fontSize: "0.7rem" }, children: u.role }), _jsx("button", { className: "btn btn-primary btn-sm", disabled: addingUser === u.id, children: addingUser === u.id ? "Adding..." : "Add" })] }, u.id)))) }))] }), _jsxs("div", { className: "form-group", style: { margin: 0 }, children: [_jsx("label", { className: "form-label", children: "Role" }), _jsxs("select", { value: addRole, onChange: e => setAddRole(e.target.value), children: [_jsx("option", { value: "member", children: "Member" }), _jsx("option", { value: "workspace_admin", children: "Admin" }), _jsx("option", { value: "workspace_owner", children: "Owner" }), _jsx("option", { value: "viewer", children: "Viewer" })] })] }), _jsx("button", { className: "btn btn-secondary", onClick: () => setMemberWsId(null), style: { marginBottom: 0 }, children: "Close" })] }) })] }))] }))] }))] }));
}
