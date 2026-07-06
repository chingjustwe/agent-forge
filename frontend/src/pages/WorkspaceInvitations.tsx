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
import { useToast } from "../components/Toast";
import { Select } from "../components/Select";
import { Modal } from "../components/Modal";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { Dropdown } from "../components/Dropdown";
import { SkeletonTable } from "../components/Skeleton";

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
  const toast = useToast();
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create-form modal state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [creating, setCreating] = useState(false);
  const [copiedToken, setCopiedToken] = useState<string | null>(null);

  // Confirm dialog for revoke
  const [revokeTarget, setRevokeTarget] = useState<WorkspaceInvitation | null>(null);
  const [revoking, setRevoking] = useState(false);

  const canManage =
    currentRole === "workspace_admin" ||
    currentRole === "tenant_admin";

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

  async function handleCreate() {
    if (!currentWorkspaceId) return;
    setCreating(true);
    setError(null);
    try {
      const trimmedEmail = email.trim();
      const inv = await createWorkspaceInvitation(currentWorkspaceId, {
        email: trimmedEmail ? trimmedEmail : null,
        role,
        expires_in_days: expiresInDays,
      });
      toast.success("Invitation created", invitationLink(inv.token));
      setEmail("");
      setRole("member");
      setExpiresInDays(7);
      setShowCreateModal(false);
      await refresh();
    } catch (e: unknown) {
      toast.error("Create failed", e instanceof Error ? e.message : "Failed to create invitation");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevokeConfirm() {
    if (!currentWorkspaceId || !revokeTarget) return;
    setRevoking(true);
    try {
      await revokeWorkspaceInvitation(currentWorkspaceId, revokeTarget.id);
      setInvitations(prev => prev.filter(x => x.id !== revokeTarget.id));
      toast.success("Invitation revoked");
      setRevokeTarget(null);
    } catch (e: unknown) {
      toast.error("Revoke failed", e instanceof Error ? e.message : "Failed to revoke invitation");
    } finally {
      setRevoking(false);
    }
  }

  async function handleCopyLink(inv: WorkspaceInvitation) {
    const link = invitationLink(inv.token);
    try {
      await navigator.clipboard.writeText(link);
      setCopiedToken(inv.token);
      toast.success("Link copied", "Invite link has been copied to clipboard.");
      setTimeout(() => setCopiedToken(null), 2000);
    } catch {
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
        <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
          + New Invitation
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading ? (
        <SkeletonTable rows={5} cols={6} />
      ) : invitations.length === 0 ? (
        <EmptyState
          title="No invitations yet"
          description="Create an invitation link to invite people to this workspace."
          action={{
            label: "+ New Invitation",
            onClick: () => setShowCreateModal(true),
          }}
        />
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
                <th style={{ width: 1 }}></th>
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
                const isDisabled = inv.is_accepted || inv.is_expired;
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
                      <Dropdown
                        items={[
                          {
                            label: copiedToken === inv.token ? "Copied!" : "Copy link",
                            onClick: () => handleCopyLink(inv),
                            disabled: isDisabled,
                          },
                          {
                            label: "Revoke",
                            onClick: () => setRevokeTarget(inv),
                            variant: "danger" as const,
                            disabled: inv.is_accepted,
                          },
                        ]}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Invitation Modal */}
      <Modal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="Create Invitation"
        width="md"
        footer={
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button
              className="btn btn-secondary"
              onClick={() => setShowCreateModal(false)}
              disabled={creating}
            >
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={handleCreate}
              disabled={creating}
            >
              {creating ? "Creating..." : "Create Invitation"}
            </button>
          </div>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="form-group">
            <label className="form-label">Email (optional -- leave blank for a generic "anyone with link" invite)</label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="invitee@example.com"
            />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Role</label>
              <Select
                value={role}
                onChange={setRole}
                options={[
                  { value: "member", label: "Member" },
                  { value: "workspace_admin", label: "Workspace Admin" },
                ]}
              />
            </div>
            <div className="form-group">
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
        </div>
      </Modal>

      {/* Revoke Confirm Dialog */}
      <ConfirmDialog
        open={!!revokeTarget}
        onClose={() => setRevokeTarget(null)}
        onConfirm={handleRevokeConfirm}
        title="Revoke Invitation"
        description={`Revoke invitation for ${revokeTarget?.email || "anyone"}? The link will stop working immediately.`}
        confirmText="Revoke"
        variant="danger"
        loading={revoking}
      />
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
