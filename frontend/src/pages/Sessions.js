import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { createSession, deleteSession, getCurrentUser, getSession, listSessions, streamChat, updateSession, } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
export default function Sessions() {
    const { sessionId } = useParams();
    return sessionId ? (_jsx(SessionDetail, { sessionId: sessionId })) : (_jsx(SessionList, {}));
}
// ---------------------------------------------------------------------------
// List view
// ---------------------------------------------------------------------------
function SessionList() {
    const { currentWorkspaceId, currentRole } = useWorkspace();
    const [sessions, setSessions] = useState([]);
    const [user, setUser] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [creating, setCreating] = useState(false);
    const navigate = useNavigate();
    async function refresh() {
        if (!currentWorkspaceId)
            return;
        setLoading(true);
        setError(null);
        try {
            const list = await listSessions(currentWorkspaceId);
            setSessions(list);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load sessions");
        }
        finally {
            setLoading(false);
        }
    }
    useEffect(() => {
        refresh();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentWorkspaceId]);
    useEffect(() => {
        getCurrentUser().then(setUser).catch(() => { });
    }, []);
    function canMutate(s) {
        if (!user)
            return false;
        if (user.role === "tenant_admin")
            return true;
        if (currentRole === "workspace_admin" || currentRole === "workspace_owner")
            return true;
        return s.owner_id === user.id;
    }
    async function handleCreate() {
        if (!currentWorkspaceId)
            return;
        setCreating(true);
        setError(null);
        try {
            const s = await createSession(currentWorkspaceId, { title: "New Chat" });
            navigate(`/sessions/${s.id}`);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to create session");
        }
        finally {
            setCreating(false);
        }
    }
    async function handleDelete(e, s) {
        e.stopPropagation();
        if (!currentWorkspaceId)
            return;
        if (!confirm(`Delete session "${s.title}"? This cannot be undone.`))
            return;
        try {
            await deleteSession(currentWorkspaceId, s.id);
            setSessions(prev => prev.filter(x => x.id !== s.id));
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to delete session");
        }
    }
    async function handleToggleVisibility(e, s) {
        e.stopPropagation();
        if (!currentWorkspaceId)
            return;
        const next = s.visibility === "private" ? "workspace" : "private";
        try {
            const updated = await updateSession(currentWorkspaceId, s.id, { visibility: next });
            setSessions(prev => prev.map(x => (x.id === s.id ? updated : x)));
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to update session");
        }
    }
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Chat Sessions" }), _jsx("p", { className: "page-subtitle", children: "Persistent conversations across page refreshes" })] }), _jsx("div", { className: "alert alert-info", children: "No workspace selected. Please select a workspace from the sidebar." })] }));
    }
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start" }, children: [_jsxs("div", { children: [_jsx("h1", { className: "page-title", children: "Chat Sessions" }), _jsx("p", { className: "page-subtitle", children: "Persistent conversations across page refreshes" })] }), _jsx("button", { className: "btn btn-primary", onClick: handleCreate, disabled: creating, children: creating ? "Creating..." : "+ New Session" })] }), error && _jsx("div", { className: "alert alert-error", children: error }), loading && _jsx("div", { className: "alert alert-info", children: "Loading sessions..." }), !loading && sessions.length === 0 && (_jsx("div", { className: "alert alert-info", children: "No sessions yet. Click \"New Session\" to start." })), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Title" }), _jsx("th", { children: "Visibility" }), _jsx("th", { children: "Owner" }), _jsx("th", { children: "Updated" }), _jsx("th", { style: { width: 1 }, children: "Actions" })] }) }), _jsx("tbody", { children: sessions.map(s => (_jsxs("tr", { className: "clickable", onClick: () => navigate(`/sessions/${s.id}`), children: [_jsx("td", { children: s.title }), _jsx("td", { children: _jsx("span", { className: `badge ${s.visibility === "workspace" ? "badge-info" : "badge-warning"}`, children: s.visibility }) }), _jsx("td", { style: { color: "var(--text-secondary)", fontSize: "0.82rem" }, children: s.owner_id === user?.id ? "you" : s.owner_id.slice(0, 8) }), _jsx("td", { style: { color: "var(--text-secondary)", fontSize: "0.82rem" }, children: formatTimestamp(s.updated_at) }), _jsx("td", { children: canMutate(s) && (_jsxs("div", { style: { display: "flex", gap: 6 }, children: [_jsx("button", { className: "btn btn-secondary", style: { padding: "4px 10px", fontSize: "0.78rem" }, onClick: e => handleToggleVisibility(e, s), title: "Toggle visibility", children: s.visibility === "private" ? "Make shared" : "Make private" }), _jsx("button", { className: "btn btn-secondary", style: { padding: "4px 10px", fontSize: "0.78rem" }, onClick: e => handleDelete(e, s), title: "Delete session", children: "Delete" })] })) })] }, s.id))) })] }) })] }));
}
// ---------------------------------------------------------------------------
// Detail view — load history + continue the conversation
// ---------------------------------------------------------------------------
function SessionDetail({ sessionId }) {
    const { currentWorkspaceId, currentRole } = useWorkspace();
    const [session, setSession] = useState(null);
    const [messages, setMessages] = useState([]);
    const [user, setUser] = useState(null);
    const [input, setInput] = useState("");
    const [streaming, setStreaming] = useState(false);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [editingTitle, setEditingTitle] = useState(false);
    const [titleDraft, setTitleDraft] = useState("");
    const messagesEndRef = useRef(null);
    const navigate = useNavigate();
    async function loadSession() {
        if (!currentWorkspaceId)
            return;
        setLoading(true);
        setError(null);
        try {
            const data = await getSession(currentWorkspaceId, sessionId);
            setSession(data.session);
            setMessages(data.messages);
            setTitleDraft(data.session.title);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load session");
        }
        finally {
            setLoading(false);
        }
    }
    useEffect(() => {
        loadSession();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentWorkspaceId, sessionId]);
    useEffect(() => {
        getCurrentUser().then(setUser).catch(() => { });
    }, []);
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages]);
    const canMutate = !!session &&
        !!user &&
        (user.role === "tenant_admin" ||
            currentRole === "workspace_admin" ||
            currentRole === "workspace_owner" ||
            session.owner_id === user.id);
    const sendMessage = useCallback(async () => {
        if (!input.trim() || streaming || !currentWorkspaceId || !session)
            return;
        const userContent = input;
        setInput("");
        setStreaming(true);
        setError(null);
        // Optimistic append of the user message; the persisted copy will be
        // re-fetched if the user refreshes.
        const tempId = `tmp-${Date.now()}`;
        setMessages(prev => [
            ...prev,
            { id: tempId, session_id: session.id, role: "user", content: userContent, tokens: 0, created_at: new Date().toISOString() },
        ]);
        let assistantContent = "";
        const asstTempId = `tmp-asst-${Date.now()}`;
        setMessages(prev => [
            ...prev,
            { id: asstTempId, session_id: session.id, role: "assistant", content: "", tokens: 0, created_at: new Date().toISOString() },
        ]);
        try {
            const history = messages
                .filter(m => m.role === "user" || m.role === "assistant")
                .map(m => ({ role: m.role, content: m.content }));
            history.push({ role: "user", content: userContent });
            for await (const event of streamChat(history, { workspace_id: currentWorkspaceId }, session.id)) {
                if (event.type === "text") {
                    assistantContent += event.data.content;
                    setMessages(prev => prev.map(m => (m.id === asstTempId ? { ...m, content: assistantContent } : m)));
                }
                else if (event.type === "error") {
                    const errMsg = event.data?.message || "Unknown error";
                    setMessages(prev => prev.map(m => m.id === asstTempId ? { ...m, content: `⚠️ Error: ${errMsg}` } : m));
                    break;
                }
            }
        }
        catch (err) {
            const errMsg = err instanceof Error ? err.message : "Network error";
            setMessages(prev => prev.map(m => m.id === asstTempId ? { ...m, content: `⚠️ Error: ${errMsg}` } : m));
        }
        finally {
            setStreaming(false);
        }
    }, [input, streaming, currentWorkspaceId, session, messages]);
    async function handleSaveTitle() {
        if (!session || !currentWorkspaceId)
            return;
        try {
            const updated = await updateSession(currentWorkspaceId, session.id, { title: titleDraft });
            setSession(updated);
            setEditingTitle(false);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to update title");
        }
    }
    async function handleDeleteSession() {
        if (!session || !currentWorkspaceId)
            return;
        if (!confirm(`Delete session "${session.title}"?`))
            return;
        try {
            await deleteSession(currentWorkspaceId, session.id);
            navigate("/sessions");
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Failed to delete session");
        }
    }
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsx("div", { className: "page-header", children: _jsx("h1", { className: "page-title", children: "Session" }) }), _jsx("div", { className: "alert alert-info", children: "No workspace selected." })] }));
    }
    if (loading) {
        return (_jsxs("div", { children: [_jsx("div", { className: "page-header", children: _jsx("h1", { className: "page-title", children: "Loading session..." }) }), _jsx("div", { className: "alert alert-info", children: "Loading..." })] }));
    }
    if (!session) {
        return (_jsxs("div", { children: [_jsx("div", { className: "page-header", children: _jsx("h1", { className: "page-title", children: "Session not found" }) }), error && _jsx("div", { className: "alert alert-error", children: error }), _jsx("button", { className: "btn btn-secondary", onClick: () => navigate("/sessions"), children: "Back to sessions" })] }));
    }
    return (_jsxs("div", { className: "chat-container", children: [_jsxs("div", { className: "page-header", style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }, children: [_jsxs("div", { style: { flex: 1, minWidth: 0 }, children: [editingTitle ? (_jsxs("div", { style: { display: "flex", gap: 6 }, children: [_jsx("input", { className: "chat-input", value: titleDraft, onChange: e => setTitleDraft(e.target.value), style: { flex: 1 }, autoFocus: true }), _jsx("button", { className: "btn btn-primary", onClick: handleSaveTitle, children: "Save" }), _jsx("button", { className: "btn btn-secondary", onClick: () => { setEditingTitle(false); setTitleDraft(session.title); }, children: "Cancel" })] })) : (_jsxs("h1", { className: "page-title", style: { cursor: canMutate ? "pointer" : "default" }, onClick: () => canMutate && setEditingTitle(true), children: [session.title, canMutate && _jsx("span", { style: { fontSize: "0.78rem", color: "var(--text-muted)", marginLeft: 8 }, children: "\u270E click to rename" })] })), _jsxs("p", { className: "page-subtitle", children: [_jsx("span", { className: `badge ${session.visibility === "workspace" ? "badge-info" : "badge-warning"}`, children: session.visibility }), " ", _jsxs("span", { style: { marginLeft: 8 }, children: [messages.length, " messages"] })] })] }), _jsx("button", { className: "btn btn-secondary", onClick: () => navigate("/sessions"), children: "\u2190 Back" })] }), error && _jsx("div", { className: "alert alert-error", children: error }), _jsxs("div", { className: "chat-messages", children: [messages.length === 0 && (_jsxs("div", { style: { textAlign: "center", padding: "60px 20px", color: "var(--text-muted)" }, children: [_jsx("div", { style: { fontSize: "2.5rem", marginBottom: 12 }, children: "\uD83D\uDCAC" }), _jsx("p", { style: { fontSize: "0.95rem" }, children: "No messages yet. Send the first message below." })] })), messages.map(m => (_jsx("div", { className: `chat-message chat-message-${m.role}`, children: _jsxs("div", { className: `chat-bubble chat-bubble-${m.role}`, children: [_jsx("div", { className: "chat-bubble-label", children: m.role }), m.content || (m.role === "assistant" && streaming ? "..." : "")] }) }, m.id))), _jsx("div", { ref: messagesEndRef })] }), _jsx("div", { className: "chat-input-area", children: canMutate ? (_jsxs(_Fragment, { children: [_jsx("input", { className: "chat-input", value: input, onChange: e => setInput(e.target.value), onKeyDown: e => e.key === "Enter" && !e.shiftKey && sendMessage(), placeholder: "Type a message...", disabled: streaming }), _jsx("button", { className: "btn btn-primary", onClick: sendMessage, disabled: streaming || !input.trim(), children: streaming ? "Sending..." : "Send" }), _jsx("button", { className: "btn btn-secondary", onClick: handleDeleteSession, title: "Delete session", children: "Delete" })] })) : (_jsx("div", { className: "alert alert-info", style: { flex: 1, margin: 0 }, children: "\uD83D\uDC41\uFE0F View only \u2014 only the session owner or a workspace admin can send messages in a shared session." })) })] }));
}
// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatTimestamp(iso) {
    if (!iso)
        return "-";
    try {
        // Backend stores UTC but SQLite drops the timezone suffix, so a naive
        // ISO string like "2026-07-04T03:50:23" is misread as local time.
        // Append "Z" when no timezone marker is present so it parses as UTC.
        const normalized = /([Z]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : iso + "Z";
        const d = new Date(normalized);
        if (isNaN(d.getTime()))
            return iso;
        const now = new Date();
        const diffMs = now.getTime() - d.getTime();
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1)
            return "just now";
        if (diffMin < 60)
            return `${diffMin}m ago`;
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24)
            return `${diffHr}h ago`;
        return d.toLocaleDateString();
    }
    catch {
        return iso;
    }
}
