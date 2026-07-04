import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  WorkspaceInvitation,
  WorkspaceInvitationPreview,
  acceptWorkspaceInvitation,
  createWorkspaceInvitation,
  getInvitationPreview,
  getToken,
  listWorkspaceInvitations,
  revokeWorkspaceInvitation,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";

export default function WorkspaceInvitations() {
  const { token } = useParams<{ token?: string }>();
  return token ? (
    <AcceptInvitationPage token={token} />
  ) : (
    <ManageInvitationsPage />
  );
}

// ---------------------------------------------------------------------------
// Manage: list + create + revoke (workspace_admin/owner)
// ---------------------------------------------------------------------------
function ManageInvitationsPage() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<"error" | "success">("error");

  // Create-form state
  const [showForm, setShowForm] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [creating, setCreating] = useState(false);
  const [copiedToken, setCopiedToken] = useState<string | null>(null);

  const canManage =
    currentRole === "workspace_admin" ||
    currentRole === "workspace_owner" ||
    currentRole === "tenant_admin";

  function showMsg(msg: string, type: "error" | "success" = "error") {
    setMessage(msg);
    setMessageType(type);
  }

  async function refresh() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const list = await listWorkspaceInvitations(currentWorkspaceId);
      setInvitations(list);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load invitations");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!currentWorkspaceId) return;
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
    } catch (e: unknown) {
      showMsg(e instanceof Error ? e.message : "Failed to create invitation");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(inv: WorkspaceInvitation) {
    if (!currentWorkspaceId) return;
    if (!confirm(`Revoke invitation for ${inv.email || "anyone"}? The link will stop working immediately.`)) return;
    try {
      await revokeWorkspaceInvitation(currentWorkspaceId, inv.id);
      setInvitations(prev => prev.filter(x => x.id !== inv.id));
      showMsg("Invitation revoked", "success");
    } catch (e: unknown) {
      showMsg(e instanceof Error ? e.message : "Failed to revoke invitation");
    }
  }

  async function handleCopyLink(inv: WorkspaceInvitation) {
    const link = invitationLink(inv.token);
    try {
      await navigator.clipboard.writeText(link);
      setCopiedToken(inv.token);
      setTimeout(() => setCopiedToken(null), 2000);
    } catch {
      // Fallback: open the link in a new window for manual copy.
      window.prompt("Copy this link:", link);
    }
  }

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Invitations</h1>
          <p className="page-subtitle">Manage workspace invite links</p>
        </div>
        <div className="alert alert-info">No workspace selected. Pick one from the sidebar.</div>
      </div>
    );
  }

  if (!canManage) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Invitations</h1>
          <p className="page-subtitle">Manage workspace invite links</p>
        </div>
        <div className="alert alert-error">
          Only workspace admins and owners can manage invitations.
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="page-title">Invitations</h1>
          <p className="page-subtitle">Generate shareable links to invite people to this workspace</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowForm(s => !s)}>
          {showForm ? "Cancel" : "+ New Invitation"}
        </button>
      </div>

      {message && <div className={`alert alert-${messageType}`}>{message}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      {showForm && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card-header">
            <h3 className="card-title">Create Invitation</h3>
          </div>
          <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="form-group">
              <label className="form-label">Email (optional — leave blank for a generic "anyone with link" invite)</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="invitee@example.com"
              />
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label">Role</label>
                <select value={role} onChange={e => setRole(e.target.value)}>
                  <option value="member">Member</option>
                  <option value="workspace_admin">Workspace Admin</option>
                  <option value="workspace_owner">Workspace Owner</option>
                </select>
              </div>
              <div className="form-group" style={{ width: 160 }}>
                <label className="form-label">Expires in (days)</label>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={expiresInDays}
                  onChange={e => setExpiresInDays(Number(e.target.value))}
                />
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" className="btn btn-primary" disabled={creating}>
                {creating ? "Creating..." : "Create Invitation"}
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => setShowForm(false)}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <div className="alert alert-info">Loading invitations...</div>
      ) : invitations.length === 0 ? (
        <div className="alert alert-info">
          No invitations yet. Click <strong>+ New Invitation</strong> to create one.
        </div>
      ) : (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Expires</th>
                <th>Created</th>
                <th style={{ width: 1 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {invitations.map(inv => {
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
                return (
                  <tr key={inv.id}>
                    <td>{inv.email || <em style={{ color: "var(--text-muted)" }}>Anyone with link</em>}</td>
                    <td><span className="badge badge-primary">{inv.role}</span></td>
                    <td><span className={`badge ${statusClass}`}>{status}</span></td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {formatDate(inv.expires_at)}
                    </td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {formatDate(inv.created_at)}
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          className="btn btn-secondary"
                          style={{ padding: "4px 10px", fontSize: "0.78rem" }}
                          onClick={() => handleCopyLink(inv)}
                          disabled={inv.is_accepted || inv.is_expired}
                          title="Copy invite link"
                        >
                          {copiedToken === inv.token ? "Copied!" : "Copy link"}
                        </button>
                        <button
                          className="btn btn-danger"
                          style={{ padding: "4px 10px", fontSize: "0.78rem" }}
                          onClick={() => handleRevoke(inv)}
                          disabled={inv.is_accepted}
                          title="Revoke invitation"
                        >
                          Revoke
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Accept page: public preview + Accept button (or Login if logged out)
// ---------------------------------------------------------------------------
function AcceptInvitationPage({ token }: { token: string }) {
  const navigate = useNavigate();
  const [preview, setPreview] = useState<WorkspaceInvitationPreview | null>(null);
  const [status, setStatus] = useState<"loading" | "not_found" | "expired" | "accepted_already" | "ready" | "accepting" | "done">("loading");
  const [error, setError] = useState<string | null>(null);
  const isLoggedIn = !!getToken();

  useEffect(() => {
    getInvitationPreview(token)
      .then(p => {
        setPreview(p);
        if (p.is_accepted) setStatus("accepted_already");
        else if (p.is_expired) setStatus("expired");
        else setStatus("ready");
      })
      .catch(() => setStatus("not_found"));
  }, [token]);

  async function handleAccept() {
    setStatus("accepting");
    setError(null);
    try {
      await acceptWorkspaceInvitation(token);
      setStatus("done");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to accept invitation");
      setStatus("ready");
    }
  }

  if (status === "loading") {
    return (
      <div className="login-page">
        <div className="login-card" style={{ textAlign: "center" }}>
          <h1 className="login-title">Validating invitation...</h1>
        </div>
      </div>
    );
  }

  if (status === "not_found") {
    return (
      <div className="login-page">
        <div className="login-card" style={{ textAlign: "center" }}>
          <h1 className="login-title">Invitation Not Found</h1>
          <p style={{ color: "var(--text-secondary)", margin: "16px 0 24px" }}>
            This invitation link is invalid or has been revoked.
          </p>
          <button className="btn btn-primary" onClick={() => navigate("/")}>Go Home</button>
        </div>
      </div>
    );
  }

  if (status === "expired") {
    return (
      <div className="login-page">
        <div className="login-card" style={{ textAlign: "center" }}>
          <h1 className="login-title">Invitation Expired</h1>
          <p style={{ color: "var(--text-secondary)", margin: "16px 0 24px" }}>
            This invitation has expired. Please ask a workspace admin for a new link.
          </p>
          <button className="btn btn-primary" onClick={() => navigate("/")}>Go Home</button>
        </div>
      </div>
    );
  }

  if (status === "accepted_already") {
    return (
      <div className="login-page">
        <div className="login-card" style={{ textAlign: "center" }}>
          <h1 className="login-title">Already Accepted</h1>
          <p style={{ color: "var(--text-secondary)", margin: "16px 0 24px" }}>
            This invitation has already been used.
          </p>
          {isLoggedIn ? (
            <button className="btn btn-primary" onClick={() => navigate("/")}>Go to Dashboard</button>
          ) : (
            <button className="btn btn-primary" onClick={() => navigate("/login")}>Sign In</button>
          )}
        </div>
      </div>
    );
  }

  if (status === "done") {
    return (
      <div className="login-page">
        <div className="login-card" style={{ textAlign: "center" }}>
          <div className="login-brand">
            <div className="login-brand-icon">A</div>
            <span className="login-brand-text">Agent Platform</span>
          </div>
          <h1 className="login-title">Welcome aboard!</h1>
          <p style={{ color: "var(--text-secondary)", margin: "16px 0 24px" }}>
            You've joined <strong>{preview?.workspace_name || "the workspace"}</strong> as <strong>{preview?.role}</strong>.
          </p>
          <button className="btn btn-primary" onClick={() => navigate("/")}>Go to Dashboard</button>
        </div>
      </div>
    );
  }

  // status === "ready" or "accepting"
  return (
    <div className="login-page">
      <div className="login-card" style={{ textAlign: "center" }}>
        <div className="login-brand">
          <div className="login-brand-icon">A</div>
          <span className="login-brand-text">Agent Platform</span>
        </div>
        <h1 className="login-title">Workspace Invitation</h1>
        <p style={{ color: "var(--text-secondary)", margin: "16px 0 24px" }}>
          You've been invited to join{" "}
          <strong>{preview?.workspace_name || "a workspace"}</strong> as{" "}
          <strong>{preview?.role}</strong>.
          {preview?.email && (
            <>
              <br />
              <span style={{ fontSize: "0.85rem" }}>
                This invitation is for <strong>{preview.email}</strong>.
              </span>
            </>
          )}
          {!preview?.email && (
            <>
              <br />
              <span style={{ fontSize: "0.85rem" }}>
                Anyone with this link may accept it.
              </span>
            </>
          )}
        </p>

        {error && <div className="alert alert-error">{error}</div>}

        {isLoggedIn ? (
          <button
            className="btn btn-primary"
            style={{ width: "100%" }}
            onClick={handleAccept}
            disabled={status === "accepting"}
          >
            {status === "accepting" ? "Accepting..." : "Accept Invitation"}
          </button>
        ) : (
          <>
            <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: 12 }}>
              Sign in to accept this invitation.
            </p>
            <button
              className="btn btn-primary"
              style={{ width: "100%" }}
              onClick={() => navigate(`/login?redirect=/invitations/${token}`)}
            >
              Sign In
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function invitationLink(token: string): string {
  const origin = window.location.origin;
  return `${origin}/invitations/${token}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "-";
  try {
    const normalized = /([Z]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : iso + "Z";
    const d = new Date(normalized);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}
