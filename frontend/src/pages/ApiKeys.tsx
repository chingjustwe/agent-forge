import { useEffect, useState } from "react";
import {
  API_KEY_SCOPES,
  ApiKeyInfo,
  ApiKeyScope,
  createApiKey,
  listApiKeys,
  revokeApiKey,
} from "../api";
import { useWorkspace } from "../context/WorkspaceContext";

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

export default function ApiKeys() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<"error" | "success">("error");

  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<CreateFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  // Plaintext key from a freshly-created API key — shown once in a modal.
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

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

  function resetForm() {
    setForm(EMPTY_FORM);
    setShowForm(false);
  }

  function toggleScope(scope: ApiKeyScope) {
    setForm(prev => ({
      ...prev,
      scopes: prev.scopes.includes(scope)
        ? prev.scopes.filter(s => s !== scope)
        : [...prev.scopes, scope],
    }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!currentWorkspaceId) return;
    if (!form.name.trim()) {
      showMsg("Name is required");
      return;
    }
    if (form.scopes.length === 0) {
      showMsg("Select at least one scope");
      return;
    }
    const days = form.expiresInDays.trim();
    const expiresInDays = days === "" ? undefined : parseInt(days, 10);
    if (expiresInDays !== undefined && (isNaN(expiresInDays) || expiresInDays < 1 || expiresInDays > 365)) {
      showMsg("Expires in days must be between 1 and 365 (or leave blank for no expiry)");
      return;
    }

    setSaving(true);
    setMessage(null);
    try {
      const created = await createApiKey(currentWorkspaceId, {
        name: form.name.trim(),
        scopes: form.scopes,
        expires_in_days: expiresInDays,
      });
      setNewKey(created.key);
      setCopied(false);
      resetForm();
      await refresh();
      showMsg("API key created", "success");
    } catch (err: unknown) {
      showMsg(err instanceof Error ? err.message : "Failed to create API key");
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

  async function handleRevoke(key: ApiKeyInfo) {
    if (!currentWorkspaceId) return;
    if (!confirm(`Revoke API key "${key.name}"? It will stop working immediately.`)) return;
    try {
      await revokeApiKey(currentWorkspaceId, key.id);
      setKeys(prev => prev.filter(k => k.id !== key.id));
      showMsg("API key revoked", "success");
    } catch (err: unknown) {
      showMsg(err instanceof Error ? err.message : "Failed to revoke API key");
    }
  }

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">API Keys</h1>
          <p className="page-subtitle">Workspace-scoped API keys for programmatic access</p>
        </div>
        <div className="alert alert-info">No workspace selected. Pick one from the sidebar.</div>
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
          <button className="btn btn-primary" onClick={() => (showForm ? resetForm() : setShowForm(true))}>
            {showForm ? "Cancel" : "+ New API Key"}
          </button>
        )}
      </div>

      {message && <div className={`alert alert-${messageType}`}>{message}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      {showForm && canManage && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card-header">
            <h3 className="card-title">Create API Key</h3>
          </div>
          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
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
                {API_KEY_SCOPES.map(s => (
                  <label key={s.value} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.9rem" }}>
                    <input
                      type="checkbox"
                      checked={form.scopes.includes(s.value)}
                      onChange={() => toggleScope(s.value)}
                    />
                    {s.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="form-group" style={{ width: 200 }}>
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
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? "Creating..." : "Create API Key"}
              </button>
              <button type="button" className="btn btn-secondary" onClick={resetForm}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <div className="alert alert-info">Loading API keys...</div>
      ) : keys.length === 0 ? (
        <div className="alert alert-info">
          No API keys yet. {canManage && "Create your first key."}
        </div>
      ) : (
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
                    <td><code style={{ fontSize: "0.85rem" }}>{k.key_prefix}…</code></td>
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
                          <button
                            className="btn btn-danger"
                            style={{ padding: "4px 10px", fontSize: "0.78rem" }}
                            onClick={() => handleRevoke(k)}
                          >
                            Revoke
                          </button>
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

      {newKey && (
        <div className="modal-backdrop" onClick={() => setNewKey(null)}>
          <div className="card modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 560 }}>
            <div className="card-header">
              <h3 className="card-title">Copy your API key</h3>
            </div>
            <div className="alert alert-warning" style={{ marginTop: 8 }}>
              Copy this key now. You won&rsquo;t be able to see it again.
            </div>
            <div className="form-group" style={{ marginTop: 12 }}>
              <textarea
                readOnly
                value={newKey}
                rows={2}
                style={{ fontFamily: "monospace", fontSize: "0.9rem" }}
                onFocus={e => e.target.select()}
              />
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button className="btn btn-primary" onClick={copyNewKey}>
                {copied ? "Copied!" : "Copy"}
              </button>
              <button className="btn btn-secondary" onClick={() => setNewKey(null)}>
                Done
              </button>
            </div>
          </div>
        </div>
      )}
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
