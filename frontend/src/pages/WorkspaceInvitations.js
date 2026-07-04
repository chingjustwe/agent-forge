import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { acceptWorkspaceInvitation, createWorkspaceInvitation, getInvitationPreview, getToken, listWorkspaceInvitations, revokeWorkspaceInvitation, } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
export default function WorkspaceInvitations() {
    const { token } = useParams();
    return token ? (_jsx(AcceptInvitationPage, { token: token })) : (_jsx(ManageInvitationsPage, {}));
}
// ---------------------------------------------------------------------------
// Manage: list + create + revoke (workspace_admin/owner)
// ---------------------------------------------------------------------------
function ManageInvitationsPage() {
    const { currentWorkspaceId, currentRole } = useWorkspace();
    const [invitations, setInvitations] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [message, setMessage] = useState(null);
    const [messageType, setMessageType] = useState("error");
    // Create-form state
    const [showForm, setShowForm] = useState(false);
    const [email, setEmail] = useState("");
    const [role, setRole] = useState("member");
    const [expiresInDays, setExpiresInDays] = useState(7);
    const [creating, setCreating] = useState(false);
    const [copiedToken, setCopiedToken] = useState(null);
    const canManage = currentRole === "workspace_admin" ||
        currentRole === "workspace_owner" ||
        currentRole === "tenant_admin";
    function showMsg(msg, type = "error") {
        setMessage(msg);
        setMessageType(type);
    }
    async function refresh() {
        if (!currentWorkspaceId)
            return;
        setLoading(true);
        setError(null);
        try {
            const list = await listWorkspaceInvitations(currentWorkspaceId);
            setInvitations(list);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load invitations");
        }
        finally {
            setLoading(false);
        }
    }
    useEffect(() => {
        refresh();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentWorkspaceId]);
    async function handleCreate(e) {
        e.preventDefault();
        if (!currentWorkspaceId)
            return;
        setCreating(true);
        setError(null);
        setMessage(null);
        try {
            const trimmedEmail = email.trim();
            const inv = await createWorkspaceInvitation(currentWorkspaceId, {
                email: trimmedEmail ? trimmedEmail : null,
                role,
                expires_in_days: expiresInDays,
            });
            showMsg(`Invitation created: ${invitationLink(inv.token)}`, "success");
            setEmail("");
            setRole("member");
            setExpiresInDays(7);
            setShowForm(false);
            await refresh();
        }
        catch (e) {
            showMsg(e instanceof Error ? e.message : "Failed to create invitation");
        }
        finally {
            setCreating(false);
        }
    }
    async function handleRevoke(inv) {
        if (!currentWorkspaceId)
            return;
        if (!confirm(`Revoke invitation for ${inv.email || "anyone"}? The link will stop working immediately.`))
            return;
        try {
            await revokeWorkspaceInvitation(currentWorkspaceId, inv.id);
            setInvitations(prev => prev.filter(x => x.id !== inv.id));
            showMsg("Invitation revoked", "success");
        }
        catch (e) {
            showMsg(e instanceof Error ? e.message : "Failed to revoke invitation");
        }
    }
    async function handleCopyLink(inv) {
        const link = invitationLink(inv.token);
        try {
            await navigator.clipboard.writeText(link);
            setCopiedToken(inv.token);
            setTimeout(() => setCopiedToken(null), 2000);
        }
        catch {
            // Fallback: open the link in a new window for manual copy.
            window.prompt("Copy this link:", link);
        }
    }
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Invitations" }), _jsx("p", { className: "page-subtitle", children: "Manage workspace invite links" })] }), _jsx("div", { className: "alert alert-info", children: "No workspace selected. Pick one from the sidebar." })] }));
    }
    if (!canManage) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Invitations" }), _jsx("p", { className: "page-subtitle", children: "Manage workspace invite links" })] }), _jsx("div", { className: "alert alert-error", children: "Only workspace admins and owners can manage invitations." })] }));
    }
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start" }, children: [_jsxs("div", { children: [_jsx("h1", { className: "page-title", children: "Invitations" }), _jsx("p", { className: "page-subtitle", children: "Generate shareable links to invite people to this workspace" })] }), _jsx("button", { className: "btn btn-primary", onClick: () => setShowForm(s => !s), children: showForm ? "Cancel" : "+ New Invitation" })] }), message && _jsx("div", { className: `alert alert-${messageType}`, children: message }), error && _jsx("div", { className: "alert alert-error", children: error }), showForm && (_jsxs("div", { className: "card", style: { marginBottom: 20 }, children: [_jsx("div", { className: "card-header", children: _jsx("h3", { className: "card-title", children: "Create Invitation" }) }), _jsxs("form", { onSubmit: handleCreate, style: { display: "flex", flexDirection: "column", gap: 10 }, children: [_jsxs("div", { className: "form-group", children: [_jsx("label", { className: "form-label", children: "Email (optional \u2014 leave blank for a generic \"anyone with link\" invite)" }), _jsx("input", { type: "email", value: email, onChange: e => setEmail(e.target.value), placeholder: "invitee@example.com" })] }), _jsxs("div", { style: { display: "flex", gap: 10 }, children: [_jsxs("div", { className: "form-group", style: { flex: 1 }, children: [_jsx("label", { className: "form-label", children: "Role" }), _jsxs("select", { value: role, onChange: e => setRole(e.target.value), children: [_jsx("option", { value: "member", children: "Member" }), _jsx("option", { value: "workspace_admin", children: "Workspace Admin" }), _jsx("option", { value: "workspace_owner", children: "Workspace Owner" })] })] }), _jsxs("div", { className: "form-group", style: { width: 160 }, children: [_jsx("label", { className: "form-label", children: "Expires in (days)" }), _jsx("input", { type: "number", min: 1, max: 365, value: expiresInDays, onChange: e => setExpiresInDays(Number(e.target.value)) })] })] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("button", { type: "submit", className: "btn btn-primary", disabled: creating, children: creating ? "Creating..." : "Create Invitation" }), _jsx("button", { type: "button", className: "btn btn-secondary", onClick: () => setShowForm(false), children: "Cancel" })] })] })] })), loading ? (_jsx("div", { className: "alert alert-info", children: "Loading invitations..." })) : invitations.length === 0 ? (_jsxs("div", { className: "alert alert-info", children: ["No invitations yet. Click ", _jsx("strong", { children: "+ New Invitation" }), " to create one."] })) : (_jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Email" }), _jsx("th", { children: "Role" }), _jsx("th", { children: "Status" }), _jsx("th", { children: "Expires" }), _jsx("th", { children: "Created" }), _jsx("th", { style: { width: 1 }, children: "Actions" })] }) }), _jsx("tbody", { children: invitations.map(inv => {
                                const status = inv.is_accepted
                                    ? "accepted"
                                    : inv.is_expired
                                        ? "expired"
                                        : "pending";
                                const statusClass = inv.is_accepted
                                    ? "badge-success"
                                    : inv.is_expired
                                        ? "badge-error"
                                        : "badge-warning";
                                return (_jsxs("tr", { children: [_jsx("td", { children: inv.email || _jsx("em", { style: { color: "var(--text-muted)" }, children: "Anyone with link" }) }), _jsx("td", { children: _jsx("span", { className: "badge badge-primary", children: inv.role }) }), _jsx("td", { children: _jsx("span", { className: `badge ${statusClass}`, children: status }) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: formatDate(inv.expires_at) }), _jsx("td", { style: { fontSize: "0.82rem", color: "var(--text-secondary)" }, children: formatDate(inv.created_at) }), _jsx("td", { children: _jsxs("div", { style: { display: "flex", gap: 6 }, children: [_jsx("button", { className: "btn btn-secondary", style: { padding: "4px 10px", fontSize: "0.78rem" }, onClick: () => handleCopyLink(inv), disabled: inv.is_accepted || inv.is_expired, title: "Copy invite link", children: copiedToken === inv.token ? "Copied!" : "Copy link" }), _jsx("button", { className: "btn btn-danger", style: { padding: "4px 10px", fontSize: "0.78rem" }, onClick: () => handleRevoke(inv), disabled: inv.is_accepted, title: "Revoke invitation", children: "Revoke" })] }) })] }, inv.id));
                            }) })] }) }))] }));
}
// ---------------------------------------------------------------------------
// Accept page: public preview + Accept button (or Login if logged out)
// ---------------------------------------------------------------------------
function AcceptInvitationPage({ token }) {
    const navigate = useNavigate();
    const [preview, setPreview] = useState(null);
    const [status, setStatus] = useState("loading");
    const [error, setError] = useState(null);
    const isLoggedIn = !!getToken();
    useEffect(() => {
        getInvitationPreview(token)
            .then(p => {
            setPreview(p);
            if (p.is_accepted)
                setStatus("accepted_already");
            else if (p.is_expired)
                setStatus("expired");
            else
                setStatus("ready");
        })
            .catch(() => setStatus("not_found"));
    }, [token]);
    async function handleAccept() {
        setStatus("accepting");
        setError(null);
        try {
            await acceptWorkspaceInvitation(token);
            setStatus("done");
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to accept invitation");
            setStatus("ready");
        }
    }
    if (status === "loading") {
        return (_jsx("div", { className: "login-page", children: _jsx("div", { className: "login-card", style: { textAlign: "center" }, children: _jsx("h1", { className: "login-title", children: "Validating invitation..." }) }) }));
    }
    if (status === "not_found") {
        return (_jsx("div", { className: "login-page", children: _jsxs("div", { className: "login-card", style: { textAlign: "center" }, children: [_jsx("h1", { className: "login-title", children: "Invitation Not Found" }), _jsx("p", { style: { color: "var(--text-secondary)", margin: "16px 0 24px" }, children: "This invitation link is invalid or has been revoked." }), _jsx("button", { className: "btn btn-primary", onClick: () => navigate("/"), children: "Go Home" })] }) }));
    }
    if (status === "expired") {
        return (_jsx("div", { className: "login-page", children: _jsxs("div", { className: "login-card", style: { textAlign: "center" }, children: [_jsx("h1", { className: "login-title", children: "Invitation Expired" }), _jsx("p", { style: { color: "var(--text-secondary)", margin: "16px 0 24px" }, children: "This invitation has expired. Please ask a workspace admin for a new link." }), _jsx("button", { className: "btn btn-primary", onClick: () => navigate("/"), children: "Go Home" })] }) }));
    }
    if (status === "accepted_already") {
        return (_jsx("div", { className: "login-page", children: _jsxs("div", { className: "login-card", style: { textAlign: "center" }, children: [_jsx("h1", { className: "login-title", children: "Already Accepted" }), _jsx("p", { style: { color: "var(--text-secondary)", margin: "16px 0 24px" }, children: "This invitation has already been used." }), isLoggedIn ? (_jsx("button", { className: "btn btn-primary", onClick: () => navigate("/"), children: "Go to Dashboard" })) : (_jsx("button", { className: "btn btn-primary", onClick: () => navigate("/login"), children: "Sign In" }))] }) }));
    }
    if (status === "done") {
        return (_jsx("div", { className: "login-page", children: _jsxs("div", { className: "login-card", style: { textAlign: "center" }, children: [_jsxs("div", { className: "login-brand", children: [_jsx("div", { className: "login-brand-icon", children: "A" }), _jsx("span", { className: "login-brand-text", children: "Agent Platform" })] }), _jsx("h1", { className: "login-title", children: "Welcome aboard!" }), _jsxs("p", { style: { color: "var(--text-secondary)", margin: "16px 0 24px" }, children: ["You've joined ", _jsx("strong", { children: preview?.workspace_name || "the workspace" }), " as ", _jsx("strong", { children: preview?.role }), "."] }), _jsx("button", { className: "btn btn-primary", onClick: () => navigate("/"), children: "Go to Dashboard" })] }) }));
    }
    // status === "ready" or "accepting"
    return (_jsx("div", { className: "login-page", children: _jsxs("div", { className: "login-card", style: { textAlign: "center" }, children: [_jsxs("div", { className: "login-brand", children: [_jsx("div", { className: "login-brand-icon", children: "A" }), _jsx("span", { className: "login-brand-text", children: "Agent Platform" })] }), _jsx("h1", { className: "login-title", children: "Workspace Invitation" }), _jsxs("p", { style: { color: "var(--text-secondary)", margin: "16px 0 24px" }, children: ["You've been invited to join", " ", _jsx("strong", { children: preview?.workspace_name || "a workspace" }), " as", " ", _jsx("strong", { children: preview?.role }), ".", preview?.email && (_jsxs(_Fragment, { children: [_jsx("br", {}), _jsxs("span", { style: { fontSize: "0.85rem" }, children: ["This invitation is for ", _jsx("strong", { children: preview.email }), "."] })] })), !preview?.email && (_jsxs(_Fragment, { children: [_jsx("br", {}), _jsx("span", { style: { fontSize: "0.85rem" }, children: "Anyone with this link may accept it." })] }))] }), error && _jsx("div", { className: "alert alert-error", children: error }), isLoggedIn ? (_jsx("button", { className: "btn btn-primary", style: { width: "100%" }, onClick: handleAccept, disabled: status === "accepting", children: status === "accepting" ? "Accepting..." : "Accept Invitation" })) : (_jsxs(_Fragment, { children: [_jsx("p", { style: { fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: 12 }, children: "Sign in to accept this invitation." }), _jsx("button", { className: "btn btn-primary", style: { width: "100%" }, onClick: () => navigate(`/login?redirect=/invitations/${token}`), children: "Sign In" })] }))] }) }));
}
// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function invitationLink(token) {
    const origin = window.location.origin;
    return `${origin}/invitations/${token}`;
}
function formatDate(iso) {
    if (!iso)
        return "-";
    try {
        const normalized = /([Z]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : iso + "Z";
        const d = new Date(normalized);
        if (isNaN(d.getTime()))
            return iso;
        return d.toLocaleString();
    }
    catch {
        return iso;
    }
}
