import { useEffect, useState } from "react";
import {
  ApiKeyInfo,
  ApiKeyScope,
  createApiKey,
  fetchPermissions,
  listApiKeys,
  revokeApiKey,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { Dropdown } from "../components/Dropdown";
import { SkeletonTable } from "../components/Skeleton";

interface CreateFormState {
  name: string;
  scopes: ApiKeyScope[];
  expiresInDays: string; // "" = never expire
}

const EMPTY_FORM: CreateFormState = {
  name: "",
  scopes: ["chat:write"],
  expiresInDays: "",
};

function scopeLabel(scope: string): string {
  const [resource, action] = scope.split(":");
  return `${resource} (${action})`;
}

export default function ApiKeys() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const toast = useToast();

  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create modal state
  const [formModalOpen, setFormModalOpen] = useState(false);
  const [form, setForm] = useState<CreateFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  // Plaintext key from a freshly-created API key — shown once in a modal.
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [availableScopes, setAvailableScopes] = useState<string[]>([]);

  // Revoke confirmation state
  const [revokeTarget, setRevokeTarget] = useState<ApiKeyInfo | null>(null);

  const canManage =
    currentRole === "workspace_admin" ||
    currentRole === "tenant_admin";

  async function refresh() {
    if (!currentWorkspaceId) return;
    setLoading(true);
    setError(null);
    try {
      const list = await listApiKeys(currentWorkspaceId);
      setKeys(list);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load API keys");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId]);

  useEffect(() => {
    fetchPermissions()
      .then(resp => setAvailableScopes(resp.api_key_scopes || []))
      .catch(() => {});
  }, []);

  function openCreateModal() {
    setForm(EMPTY_FORM);
    setFormModalOpen(true);
  }

  function toggleScope(scope: ApiKeyScope) {
    setForm(prev => ({
      ...prev,
      scopes: prev.scopes.includes(scope)
        ? prev.scopes.filter(s => s !== scope)
        : [...prev.scopes, scope],
    }));
  }

  async function handleCreateSubmit() {
    if (!currentWorkspaceId) return;
    if (!form.name.trim()) {
      toast.error("Validation error", "Name is required");
      return;
    }
    if (form.scopes.length === 0) {
      toast.error("Validation error", "Select at least one scope");
      return;
    }
    const days = form.expiresInDays.trim();
    const expiresInDays = days === "" ? undefined : parseInt(days, 10);
    if (expiresInDays !== undefined && (isNaN(expiresInDays) || expiresInDays < 1 || expiresInDays > 365)) {
      toast.error("Validation error", "Expires in days must be between 1 and 365 (or leave blank for no expiry)");
      return;
    }

    setSaving(true);
    try {
      const created = await createApiKey(currentWorkspaceId, {
        name: form.name.trim(),
        scopes: form.scopes,
        expires_in_days: expiresInDays,
      });
      setNewKey(created.key);
      setCopied(false);
      setFormModalOpen(false);
      setForm(EMPTY_FORM);
      await refresh();
      toast.success("API key created");
    } catch (err: unknown) {
      toast.error("Failed to create API key", err instanceof Error ? err.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  async function copyNewKey() {
    if (!newKey) return;
    try {
      await navigator.clipboard.writeText(newKey);
      setCopied(true);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fall back to
      // selecting the text input so the user can manually copy.
      setCopied(false);
    }
  }

  function openRevokeConfirm(key: ApiKeyInfo) {
    setRevokeTarget(key);
  }

  async function handleRevokeConfirm() {
    if (!revokeTarget || !currentWorkspaceId) return;
    try {
      await revokeApiKey(currentWorkspaceId, revokeTarget.id);
      setKeys(prev => prev.filter(k => k.id !== revokeTarget.id));
      toast.success("API key revoked");
      setRevokeTarget(null);
    } catch (err: unknown) {
      toast.error("Failed to revoke API key", err instanceof Error ? err.message : undefined);
    }
  }

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">API Keys</h1>
          <p className="page-subtitle">Workspace-scoped API keys for programmatic access</p>
        </div>
        <EmptyState
          title="No workspace selected"
          description="Pick one from the sidebar."
        />
      </div>
    );
  }

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="page-title">API Keys</h1>
          <p className="page-subtitle">Manage API keys bound to this workspace</p>
        </div>
        {canManage && (
          <button className="btn btn-primary" onClick={openCreateModal}>
            + New API Key
          </button>
        )}
      </div>

      {error && <EmptyState title="Error loading API keys" description={error} />}

      {!error && loading && <SkeletonTable rows={5} cols={7} />}

      {!error && !loading && keys.length === 0 && (
        <EmptyState
          title="No API keys yet"
          description={canManage ? "Create your first key to get started." : "No API keys have been created for this workspace."}
          action={canManage ? { label: "+ New API Key", onClick: openCreateModal } : undefined}
        />
      )}

      {!error && !loading && keys.length > 0 && (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Scopes</th>
                <th>Status</th>
                <th>Last used</th>
                <th>Expires</th>
                <th>Created</th>
                {canManage && <th style={{ width: 1 }}>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {keys.map(k => {
                const status = keyStatus(k);
                return (
                  <tr key={k.id}>
                    <td>{k.name}</td>
                    <td><code style={{ fontSize: "0.85rem" }}>{k.key_prefix}&hellip;</code></td>
                    <td>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                        {(k.scopes || []).map(s => (
                          <span key={s} className="badge badge-primary" style={{ fontSize: "0.72rem" }}>{s}</span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <span className={`badge badge-${status.tone}`}>{status.label}</span>
                    </td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {formatDate(k.last_used_at)}
                    </td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {k.expires_at ? formatDate(k.expires_at) : "never"}
                    </td>
                    <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                      {formatDate(k.created_at)}
                    </td>
                    {canManage && (
                      <td>
                        {!k.revoked && (
                          <Dropdown items={[
                            { label: "Revoke", onClick: () => openRevokeConfirm(k), variant: "danger" },
                          ]} />
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Create API Key Modal */}
      <Modal
        open={formModalOpen}
        onClose={() => setFormModalOpen(false)}
        title="Create API Key"
        width="md"
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setFormModalOpen(false)} disabled={saving}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={handleCreateSubmit} disabled={saving}>
              {saving ? "Creating..." : "Create API Key"}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="form-group">
            <label className="form-label">Name</label>
            <input
              type="text"
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              maxLength={100}
              placeholder="e.g. CI pipeline key"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Scopes</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
              {availableScopes.map(s => (
                <label key={s} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.9rem" }}>
                  <input
                    type="checkbox"
                    checked={form.scopes.includes(s)}
                    onChange={() => toggleScope(s)}
                  />
                  {scopeLabel(s)}
                </label>
              ))}
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Expires in days (blank = never)</label>
            <input
              type="number"
              min={1}
              max={365}
              value={form.expiresInDays}
              onChange={e => setForm({ ...form, expiresInDays: e.target.value })}
              placeholder="never"
            />
          </div>
        </div>
      </Modal>

      {/* New Key Reveal Modal */}
      <Modal
        open={!!newKey}
        onClose={() => setNewKey(null)}
        title="Copy Your API Key"
        width="sm"
        footer={
          <>
            <button className="btn btn-primary" onClick={copyNewKey}>
              {copied ? "Copied!" : "Copy"}
            </button>
            <button className="btn btn-secondary" onClick={() => setNewKey(null)}>
              Done
            </button>
          </>
        }
      >
        <div className="confirm-dialog" style={{ marginBottom: 16, display: "flex", alignItems: "flex-start", gap: 12 }}>
          <div className="confirm-dialog-icon" style={{ flexShrink: 0 }}>
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <div>
            <div className="confirm-dialog-title">Save this key now</div>
            <div className="confirm-dialog-description">
              Copy this key now. You won&rsquo;t be able to see it again.
            </div>
          </div>
        </div>
        <div className="form-group">
          <textarea
            readOnly
            value={newKey || ""}
            rows={2}
            style={{ fontFamily: "monospace", fontSize: "0.9rem" }}
            onFocus={e => e.target.select()}
          />
        </div>
      </Modal>

      {/* Revoke Confirmation */}
      <ConfirmDialog
        open={!!revokeTarget}
        onClose={() => setRevokeTarget(null)}
        onConfirm={handleRevokeConfirm}
        title="Revoke API Key"
        description={`Revoke API key "${revokeTarget?.name}"? It will stop working immediately.`}
        confirmText="Revoke"
        variant="danger"
      />
    </div>
  );
}

function keyStatus(k: ApiKeyInfo): { label: string; tone: string } {
  if (k.revoked) return { label: "Revoked", tone: "error" };
  if (k.expires_at) {
    try {
      const normalized = /([Z]|[+-]\d{2}:?\d{2})$/.test(k.expires_at) ? k.expires_at : k.expires_at + "Z";
      if (new Date(normalized) < new Date()) return { label: "Expired", tone: "error" };
    } catch {
      // fall through to active
    }
  }
  return { label: "Active", tone: "success" };
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
