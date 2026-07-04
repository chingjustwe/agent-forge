import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ChatMessageInfo,
  ChatSessionInfo,
  User,
  createSession,
  deleteSession,
  getCurrentUser,
  getSession,
  listSessions,
  streamChat,
  updateSession,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";

export default function Sessions() {
  const { sessionId } = useParams<{ sessionId?: string }>();
  return sessionId ? (
    <SessionDetail sessionId={sessionId} />
  ) : (
    <SessionList />
  );
}

// ---------------------------------------------------------------------------
// List view
// ---------------------------------------------------------------------------
function SessionList() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const [sessions, setSessions] = useState<ChatSessionInfo[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  async function refresh() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const list = await listSessions(currentWorkspaceId);
      setSessions(list);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load sessions");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => {});
  }, []);

  function canMutate(s: ChatSessionInfo) {
    if (!user) return false;
    if (user.role === "tenant_admin") return true;
    if (currentRole === "workspace_admin" || currentRole === "workspace_owner") return true;
    return s.owner_id === user.id;
  }

  async function handleCreate() {
    if (!currentWorkspaceId) return;
    setCreating(true);
    setError(null);
    try {
      const s = await createSession(currentWorkspaceId, { title: "New Chat" });
      navigate(`/sessions/${s.id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create session");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(e: React.MouseEvent, s: ChatSessionInfo) {
    e.stopPropagation();
    if (!currentWorkspaceId) return;
    if (!confirm(`Delete session "${s.title}"? This cannot be undone.`)) return;
    try {
      await deleteSession(currentWorkspaceId, s.id);
      setSessions(prev => prev.filter(x => x.id !== s.id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete session");
    }
  }

  async function handleToggleVisibility(e: React.MouseEvent, s: ChatSessionInfo) {
    e.stopPropagation();
    if (!currentWorkspaceId) return;
    const next = s.visibility === "private" ? "workspace" : "private";
    try {
      const updated = await updateSession(currentWorkspaceId, s.id, { visibility: next });
      setSessions(prev => prev.map(x => (x.id === s.id ? updated : x)));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update session");
    }
  }

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Chat Sessions</h1>
          <p className="page-subtitle">Persistent conversations across page refreshes</p>
        </div>
        <div className="alert alert-info">No workspace selected. Please select a workspace from the sidebar.</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="page-title">Chat Sessions</h1>
          <p className="page-subtitle">Persistent conversations across page refreshes</p>
        </div>
        <button className="btn btn-primary" onClick={handleCreate} disabled={creating}>
          {creating ? "Creating..." : "+ New Session"}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {loading && <div className="alert alert-info">Loading sessions...</div>}
      {!loading && sessions.length === 0 && (
        <div className="alert alert-info">No sessions yet. Click "New Session" to start.</div>
      )}

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Visibility</th>
              <th>Owner</th>
              <th>Updated</th>
              <th style={{ width: 1 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map(s => (
              <tr
                key={s.id}
                className="clickable"
                onClick={() => navigate(`/sessions/${s.id}`)}
              >
                <td>{s.title}</td>
                <td>
                  <span className={`badge ${s.visibility === "workspace" ? "badge-info" : "badge-warning"}`}>
                    {s.visibility}
                  </span>
                </td>
                <td style={{ color: "var(--text-secondary)", fontSize: "0.82rem" }}>
                  {s.owner_id === user?.id ? "you" : s.owner_id.slice(0, 8)}
                </td>
                <td style={{ color: "var(--text-secondary)", fontSize: "0.82rem" }}>
                  {formatTimestamp(s.updated_at)}
                </td>
                <td>
                  {canMutate(s) && (
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        className="btn btn-secondary"
                        style={{ padding: "4px 10px", fontSize: "0.78rem" }}
                        onClick={e => handleToggleVisibility(e, s)}
                        title="Toggle visibility"
                      >
                        {s.visibility === "private" ? "Make shared" : "Make private"}
                      </button>
                      <button
                        className="btn btn-secondary"
                        style={{ padding: "4px 10px", fontSize: "0.78rem" }}
                        onClick={e => handleDelete(e, s)}
                        title="Delete session"
                      >
                        Delete
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail view — load history + continue the conversation
// ---------------------------------------------------------------------------
function SessionDetail({ sessionId }: { sessionId: string }) {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const [session, setSession] = useState<ChatSessionInfo | null>(null);
  const [messages, setMessages] = useState<ChatMessageInfo[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  async function loadSession() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getSession(currentWorkspaceId, sessionId);
      setSession(data.session);
      setMessages(data.messages);
      setTitleDraft(data.session.title);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load session");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId, sessionId]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const canMutate =
    !!session &&
    !!user &&
    (user.role === "tenant_admin" ||
      currentRole === "workspace_admin" ||
      currentRole === "workspace_owner" ||
      session.owner_id === user.id);

  const sendMessage = useCallback(async () => {
    if (!input.trim() || streaming || !currentWorkspaceId || !session) return;
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
          assistantContent += event.data.content as string;
          setMessages(prev =>
            prev.map(m => (m.id === asstTempId ? { ...m, content: assistantContent } : m))
          );
        } else if (event.type === "error") {
          const errMsg = (event.data as { message?: string })?.message || "Unknown error";
          setMessages(prev =>
            prev.map(m =>
              m.id === asstTempId ? { ...m, content: `⚠️ Error: ${errMsg}` } : m
            )
          );
          break;
        }
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : "Network error";
      setMessages(prev =>
        prev.map(m =>
          m.id === asstTempId ? { ...m, content: `⚠️ Error: ${errMsg}` } : m
        )
      );
    } finally {
      setStreaming(false);
    }
  }, [input, streaming, currentWorkspaceId, session, messages]);

  async function handleSaveTitle() {
    if (!session || !currentWorkspaceId) return;
    try {
      const updated = await updateSession(currentWorkspaceId, session.id, { title: titleDraft });
      setSession(updated);
      setEditingTitle(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to update title");
    }
  }

  async function handleDeleteSession() {
    if (!session || !currentWorkspaceId) return;
    if (!confirm(`Delete session "${session.title}"?`)) return;
    try {
      await deleteSession(currentWorkspaceId, session.id);
      navigate("/sessions");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete session");
    }
  }

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Session</h1>
        </div>
        <div className="alert alert-info">No workspace selected.</div>
      </div>
    );
  }

  if (loading) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Loading session...</h1>
        </div>
        <div className="alert alert-info">Loading...</div>
      </div>
    );
  }

  if (!session) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Session not found</h1>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        <button className="btn btn-secondary" onClick={() => navigate("/sessions")}>
          Back to sessions
        </button>
      </div>
    );
  }

  return (
    <div className="chat-container">
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {editingTitle ? (
            <div style={{ display: "flex", gap: 6 }}>
              <input
                className="chat-input"
                value={titleDraft}
                onChange={e => setTitleDraft(e.target.value)}
                style={{ flex: 1 }}
                autoFocus
              />
              <button className="btn btn-primary" onClick={handleSaveTitle}>Save</button>
              <button className="btn btn-secondary" onClick={() => { setEditingTitle(false); setTitleDraft(session.title); }}>
                Cancel
              </button>
            </div>
          ) : (
            <h1 className="page-title" style={{ cursor: canMutate ? "pointer" : "default" }} onClick={() => canMutate && setEditingTitle(true)}>
              {session.title}
              {canMutate && <span style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginLeft: 8 }}>✎ click to rename</span>}
            </h1>
          )}
          <p className="page-subtitle">
            <span className={`badge ${session.visibility === "workspace" ? "badge-info" : "badge-warning"}`}>
              {session.visibility}
            </span>{" "}
            <span style={{ marginLeft: 8 }}>{messages.length} messages</span>
          </p>
        </div>
        <button className="btn btn-secondary" onClick={() => navigate("/sessions")}>← Back</button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="chat-messages">
        {messages.length === 0 && (
          <div style={{ textAlign: "center", padding: "60px 20px", color: "var(--text-muted)" }}>
            <div style={{ fontSize: "2.5rem", marginBottom: 12 }}>💬</div>
            <p style={{ fontSize: "0.95rem" }}>No messages yet. Send the first message below.</p>
          </div>
        )}
        {messages.map(m => (
          <div key={m.id} className={`chat-message chat-message-${m.role}`}>
            <div className={`chat-bubble chat-bubble-${m.role}`}>
              <div className="chat-bubble-label">{m.role}</div>
              {m.content || (m.role === "assistant" && streaming ? "..." : "")}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        {canMutate ? (
          <>
            <input
              className="chat-input"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !e.shiftKey && sendMessage()}
              placeholder="Type a message..."
              disabled={streaming}
            />
            <button className="btn btn-primary" onClick={sendMessage} disabled={streaming || !input.trim()}>
              {streaming ? "Sending..." : "Send"}
            </button>
            <button className="btn btn-secondary" onClick={handleDeleteSession} title="Delete session">
              Delete
            </button>
          </>
        ) : (
          <div className="alert alert-info" style={{ flex: 1, margin: 0 }}>
            👁️ View only — only the session owner or a workspace admin can send messages in a shared session.
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatTimestamp(iso: string): string {
  if (!iso) return "-";
  try {
    // Backend stores UTC but SQLite drops the timezone suffix, so a naive
    // ISO string like "2026-07-04T03:50:23" is misread as local time.
    // Append "Z" when no timezone marker is present so it parses as UTC.
    const normalized = /([Z]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : iso + "Z";
    const d = new Date(normalized);
    if (isNaN(d.getTime())) return iso;
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString();
  } catch {
    return iso;
  }
}
