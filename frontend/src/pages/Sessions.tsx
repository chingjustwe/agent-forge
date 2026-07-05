import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ChatMessageInfo,
  ChatSessionInfo,
  SessionShare,
  User,
  WorkspaceMember,
  createSession,
  createSessionShare,
  deleteSession,
  deleteSessionShare,
  fetchWorkspaceMembers,
  getCurrentUser,
  getSession,
  listSessionShares,
  listSessions,
  streamChat,
  updateSession,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { Dropdown } from "../components/Dropdown";
import { SkeletonTable } from "../components/Skeleton";

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
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ChatSessionInfo | null>(null);
  const toast = useToast();
  const navigate = useNavigate();

  async function refresh() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    try {
      const list = await listSessions(currentWorkspaceId);
      setSessions(list);
    } catch (e: unknown) {
      toast.error("Load failed", e instanceof Error ? e.message : "Failed to load sessions");
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
    if (currentRole === "workspace_admin") return true;
    return s.owner_id === user.id;
  }

  function canCreate() {
    if (!user) return false;
    if (user.role === "tenant_admin") return true;
    if (currentRole === "workspace_admin") return true;
    if (currentRole === "member") return true;
    return false;
  }

  async function handleCreate() {
    if (!currentWorkspaceId) return;
    setCreating(true);
    try {
      const s = await createSession(currentWorkspaceId, { title: "New Chat" });
      navigate(`/sessions/${s.id}`);
    } catch (e: unknown) {
      toast.error("Create failed", e instanceof Error ? e.message : "Failed to create session");
    } finally {
      setCreating(false);
    }
  }

  async function confirmDelete() {
    if (!currentWorkspaceId || !deleteTarget) return;
    try {
      await deleteSession(currentWorkspaceId, deleteTarget.id);
      setSessions(prev => prev.filter(x => x.id !== deleteTarget.id));
      toast.success("Deleted", `Session "${deleteTarget.title}" was deleted.`);
    } catch (err: unknown) {
      toast.error("Delete failed", err instanceof Error ? err.message : "Failed to delete session");
    } finally {
      setDeleteTarget(null);
    }
  }

  async function handleToggleVisibility(e: React.MouseEvent, s: ChatSessionInfo) {
    e.stopPropagation();
    if (!currentWorkspaceId) return;
    const next = s.visibility === "private" ? "workspace" : "private";
    try {
      const updated = await updateSession(currentWorkspaceId, s.id, { visibility: next });
      setSessions(prev => prev.map(x => (x.id === s.id ? updated : x)));
      toast.success("Updated", `Session visibility changed to ${next}.`);
    } catch (err: unknown) {
      toast.error("Update failed", err instanceof Error ? err.message : "Failed to update session");
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

      {loading && <SkeletonTable rows={5} cols={4} />}
      {!loading && sessions.length === 0 && (
        <EmptyState
          title="No Sessions Yet"
          description="Start a conversation with an agent. Your session history will appear here."
          action={canCreate() ? { label: "New Session", onClick: handleCreate } : undefined}
        />
      )}

      {!loading && sessions.length > 0 && (
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
                  <td onClick={e => e.stopPropagation()}>
                    {canMutate(s) && (
                      <Dropdown items={[
                        {
                          label: s.visibility === "private" ? "Make shared" : "Make private",
                          onClick: () => { handleToggleVisibility({ stopPropagation: () => {} } as React.MouseEvent, s); },
                        },
                        {
                          label: "Delete",
                          onClick: () => setDeleteTarget(s),
                          variant: "danger",
                        },
                      ]} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={confirmDelete}
        title="Delete Session"
        description={deleteTarget ? `Delete session "${deleteTarget.title}"? This cannot be undone.` : ""}
        confirmText="Delete"
        variant="danger"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail view -- load history + continue the conversation
// ---------------------------------------------------------------------------
function SessionDetail({ sessionId }: { sessionId: string }) {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const [session, setSession] = useState<ChatSessionInfo | null>(null);
  const [messages, setMessages] = useState<ChatMessageInfo[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [loading, setLoading] = useState(true);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();
  const toast = useToast();

  // P3-5: session sharing state.
  const [showShareModal, setShowShareModal] = useState(false);
  const [shares, setShares] = useState<SessionShare[]>([]);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [shareLoading, setShareLoading] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [sharing, setSharing] = useState(false);
  const [removingUserId, setRemovingUserId] = useState<string | null>(null);

  async function loadSession() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    try {
      const data = await getSession(currentWorkspaceId, sessionId);
      setSession(data.session);
      setMessages(data.messages);
      setTitleDraft(data.session.title);
    } catch (e: unknown) {
      toast.error("Load failed", e instanceof Error ? e.message : "Failed to load session");
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
      session.owner_id === user.id);

  const sendMessage = useCallback(async () => {
    if (!input.trim() || streaming || !currentWorkspaceId || !session) return;
    const userContent = input;
    setInput("");
    setStreaming(true);

    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    // Optimistic append of the user message.
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
              m.id === asstTempId ? { ...m, content: `Error: ${errMsg}` } : m
            )
          );
          toast.error("Chat error", errMsg);
          break;
        }
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : "Network error";
      setMessages(prev =>
        prev.map(m =>
          m.id === asstTempId ? { ...m, content: `Error: ${errMsg}` } : m
        )
      );
      toast.error("Chat error", errMsg);
    } finally {
      setStreaming(false);
    }
  }, [input, streaming, currentWorkspaceId, session, messages, toast]);

  async function handleSaveTitle() {
    if (!session || !currentWorkspaceId) return;
    try {
      const updated = await updateSession(currentWorkspaceId, session.id, { title: titleDraft });
      setSession(updated);
      setEditingTitle(false);
      toast.success("Renamed", "Session title updated.");
    } catch (e: unknown) {
      toast.error("Rename failed", e instanceof Error ? e.message : "Failed to update title");
    }
  }

  async function confirmDeleteSession() {
    if (!session || !currentWorkspaceId) return;
    try {
      await deleteSession(currentWorkspaceId, session.id);
      toast.success("Deleted", `Session "${session.title}" was deleted.`);
      navigate("/sessions");
    } catch (e: unknown) {
      toast.error("Delete failed", e instanceof Error ? e.message : "Failed to delete session");
      setDeleteConfirmOpen(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function handleInputChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value);
    // Auto-resize
    const ta = e.target;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  }

  // P3-5: open the share modal -- load current shares + workspace members.
  async function openShareModal() {
    if (!session) return;
    setShowShareModal(true);
    setShareError(null);
    setShareLoading(true);
    try {
      const [shareList, memberList] = await Promise.all([
        listSessionShares(session.id),
        currentWorkspaceId
          ? fetchWorkspaceMembers(currentWorkspaceId)
          : Promise.resolve([]),
      ]);
      setShares(shareList);
      setMembers(memberList);
      setSelectedUserId("");
    } catch (e: unknown) {
      setShareError(e instanceof Error ? e.message : "Failed to load share info");
    } finally {
      setShareLoading(false);
    }
  }

  async function handleShare() {
    if (!session || !selectedUserId) return;
    setSharing(true);
    setShareError(null);
    try {
      const newShare = await createSessionShare(session.id, selectedUserId);
      setShares(prev =>
        prev.some(s => s.user_id === newShare.user_id)
          ? prev
          : [...prev, newShare],
      );
      setSelectedUserId("");
      toast.success("Shared", "Session shared with member.");
    } catch (e: unknown) {
      setShareError(e instanceof Error ? e.message : "Failed to share session");
    } finally {
      setSharing(false);
    }
  }

  async function handleRemoveShare(userId: string) {
    if (!session) return;
    setRemovingUserId(userId);
    setShareError(null);
    try {
      await deleteSessionShare(session.id, userId);
      setShares(prev => prev.filter(s => s.user_id !== userId));
      toast.success("Revoked", "Share access revoked.");
    } catch (e: unknown) {
      setShareError(e instanceof Error ? e.message : "Failed to revoke share");
    } finally {
      setRemovingUserId(null);
    }
  }

  // Map user_id to {name, email} for resolving share rows.
  function resolveUser(userId: string): { name: string; email: string } {
    const m = members.find(x => x.user_id === userId);
    if (m) return { name: m.name, email: m.email };
    if (user && user.id === userId) return { name: user.name, email: user.email };
    return { name: userId.slice(0, 8), email: "" };
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
        <SkeletonTable rows={6} cols={2} />
      </div>
    );
  }

  if (!session) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Session not found</h1>
        </div>
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
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h1 className="page-title" style={{ cursor: "default" }}>
                {session.title}
              </h1>
              {canMutate && (
                <button
                  className="session-rename-btn"
                  onClick={() => setEditingTitle(true)}
                  title="Rename session"
                  aria-label="Rename session"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M11.5 1.5l3 3L5 14H2v-3L11.5 1.5z" />
                  </svg>
                </button>
              )}
            </div>
          )}
          <p className="page-subtitle">
            <span className={`badge ${session.visibility === "workspace" ? "badge-info" : "badge-warning"}`}>
              {session.visibility}
            </span>{" "}
            <span style={{ marginLeft: 8 }}>{messages.length} messages</span>
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {canMutate && (
            <button className="btn btn-secondary" onClick={openShareModal} title="Share this session with workspace members">
              Share
            </button>
          )}
          <button className="btn btn-secondary" onClick={() => navigate("/sessions")}>Back</button>
        </div>
      </div>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div style={{ textAlign: "center", padding: "60px 20px", color: "var(--text-muted)" }}>
            <div style={{ fontSize: "2.5rem", marginBottom: 12, opacity: 0.5 }}>
              <svg width="48" height="48" viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ margin: "0 auto", display: "block" }}>
                <rect x="8" y="12" width="32" height="24" rx="4" />
                <path d="M8 20h10l4-4h4l4 4h10" />
                <circle cx="24" cy="28" r="3" />
              </svg>
            </div>
            <p style={{ fontSize: "0.95rem" }}>No messages yet. Send the first message below.</p>
          </div>
        )}
        {messages.map(m => (
          <div key={m.id} className={`chat-message chat-message-${m.role}`}>
            <div className={`chat-bubble chat-bubble-${m.role}`}>
              {m.content || (m.role === "assistant" && streaming ? "..." : "")}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        {canMutate ? (
          <>
            <textarea
              ref={textareaRef}
              className="chat-textarea"
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
              disabled={streaming}
              rows={1}
              style={{
                resize: "none",
                overflow: "hidden",
                height: "auto",
                minHeight: "44px",
                maxHeight: "200px",
                fontFamily: "var(--font-sans)",
                fontSize: "0.88rem",
              }}
            />
            <button className="btn btn-primary" onClick={sendMessage} disabled={streaming || !input.trim()}>
              {streaming ? "Sending..." : "Send"}
            </button>
            <button className="btn btn-danger btn-sm" onClick={() => setDeleteConfirmOpen(true)} title="Delete session">
              Delete
            </button>
          </>
        ) : (
          <div className="chat-viewer-notice" style={{ flex: 1, margin: 0 }}>
            View only -- only the session owner or a workspace admin can send messages in a shared session.
          </div>
        )}
      </div>

      {/* Share Modal */}
      <Modal
        open={showShareModal}
        onClose={() => !sharing && !removingUserId && setShowShareModal(false)}
        title="Share Session"
        width="md"
      >
        <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", marginBottom: 16 }}>
          Shared members can view this session even when visibility is private.
        </p>

        {shareError && <div className="alert alert-error" style={{ margin: "0 0 12px" }}>{shareError}</div>}

        {shareLoading ? (
          <div className="loading" style={{ padding: 16 }}>Loading...</div>
        ) : (
          <>
            <div className="form-label" style={{ marginBottom: 6 }}>Currently shared with</div>
            {shares.length === 0 ? (
              <div style={{ padding: "8px 0", color: "var(--text-muted)", fontSize: "0.88rem" }}>
                Not shared with anyone yet.
              </div>
            ) : (
              <div style={{ borderTop: "1px solid var(--border-color)", marginBottom: 12 }}>
                {shares.map(s => {
                  const info = resolveUser(s.user_id);
                  const sharedBy = resolveUser(s.shared_by);
                  return (
                    <div key={s.user_id} style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "8px 0", borderBottom: "1px solid var(--border-color)",
                    }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: "0.9rem" }}>
                          {info.name}
                          {info.email && (
                            <span style={{ color: "var(--text-muted)", marginLeft: 6, fontSize: "0.8rem" }}>
                              &lt;{info.email}&gt;
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                          shared by {sharedBy.name} · {formatTimestamp(s.shared_at)}
                        </div>
                      </div>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleRemoveShare(s.user_id)}
                        disabled={removingUserId === s.user_id}
                      >
                        {removingUserId === s.user_id ? "Removing..." : "Remove"}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="form-label" style={{ marginBottom: 6 }}>Add a workspace member</div>
            {(() => {
              const sharedIds = new Set(shares.map(s => s.user_id));
              const ownerExcluded = new Set([session.owner_id, ...(user ? [user.id] : [])]);
              const eligible = members.filter(
                m => !sharedIds.has(m.user_id) && !ownerExcluded.has(m.user_id),
              );
              if (eligible.length === 0) {
                return (
                  <div style={{ padding: "8px 0", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                    No more workspace members to share with.
                  </div>
                );
              }
              return (
                <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
                  <div className="form-group" style={{ flex: 1, margin: 0 }}>
                    <select
                      value={selectedUserId}
                      onChange={e => setSelectedUserId(e.target.value)}
                    >
                      <option value="">Select a member...</option>
                      {eligible.map(m => (
                        <option key={m.user_id} value={m.user_id}>
                          {m.name || m.email} {m.email ? `<${m.email}>` : ""}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    className="btn btn-primary"
                    onClick={handleShare}
                    disabled={sharing || !selectedUserId}
                  >
                    {sharing ? "Sharing..." : "Share"}
                  </button>
                </div>
              );
            })()}
          </>
        )}
      </Modal>

      {/* Delete Confirm Dialog */}
      <ConfirmDialog
        open={deleteConfirmOpen}
        onClose={() => setDeleteConfirmOpen(false)}
        onConfirm={confirmDeleteSession}
        title="Delete Session"
        description={`Delete session "${session.title}"? This cannot be undone.`}
        confirmText="Delete"
        variant="danger"
      />
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
