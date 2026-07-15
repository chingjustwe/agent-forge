import { useEffect, useState } from "react";
import {
  createSsoProvider,
  deleteSsoProvider,
  listSsoProviders,
  SsoProviderConfig,
  SsoProviderType,
  updateSsoProvider,
} from "../api";
import { Modal } from "../components/Modal";
import { Select } from "../components/Select";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { SkeletonTable } from "../components/Skeleton";

const PROVIDER_TYPE_OPTIONS: { value: SsoProviderType; label: string }[] = [
  { value: "google", label: "Google" },
  { value: "microsoft", label: "Microsoft" },
  { value: "custom_oidc", label: "Custom OIDC" },
];

const DEFAULT_ROLE_OPTIONS = [
  { value: "member", label: "member" },
  { value: "viewer", label: "viewer" },
  { value: "workspace_admin", label: "workspace_admin" },
];

interface FormState {
  name: string;
  slug: string;
  provider_type: SsoProviderType;
  client_id: string;
  client_secret: string;
  ms_tenant: string;
  auto_provision: boolean;
  default_role: string;
  enabled: boolean;
  scopes: string;
  authorize_url: string;
  token_url: string;
  userinfo_url: string;
  issuer_url: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  slug: "",
  provider_type: "google",
  client_id: "",
  client_secret: "",
  ms_tenant: "",
  auto_provision: true,
  default_role: "member",
  enabled: true,
  scopes: "",
  authorize_url: "",
  token_url: "",
  userinfo_url: "",
  issuer_url: "",
};

function providerTypeLabel(t: SsoProviderType): string {
  return PROVIDER_TYPE_OPTIONS.find((o) => o.value === t)?.label || t;
}

export default function AdminSso() {
  const toast = useToast();
  const [providers, setProviders] = useState<SsoProviderConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create modal
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState<FormState>({ ...EMPTY_FORM });

  // Edit modal
  const [editTarget, setEditTarget] = useState<SsoProviderConfig | null>(null);
  const [editForm, setEditForm] = useState<FormState>({ ...EMPTY_FORM });
  const [savingEdit, setSavingEdit] = useState(false);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<SsoProviderConfig | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const list = await listSsoProviders();
      setProviders(list);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load SSO providers");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleCreate() {
    setCreating(true);
    try {
      const scopes = createForm.scopes
        ? createForm.scopes.split(",").map((s) => s.trim()).filter(Boolean)
        : ["openid", "email", "profile"];
      await createSsoProvider({
        name: createForm.name,
        slug: createForm.slug,
        provider_type: createForm.provider_type,
        client_id: createForm.client_id,
        client_secret: createForm.client_secret,
        ms_tenant: createForm.provider_type === "microsoft" ? createForm.ms_tenant || null : null,
        auto_provision: createForm.auto_provision,
        default_role: createForm.default_role,
        enabled: createForm.enabled,
        scopes,
        authorize_url: createForm.authorize_url || null,
        token_url: createForm.token_url || null,
        userinfo_url: createForm.userinfo_url || null,
        issuer_url: createForm.issuer_url || null,
      });
      toast.success("SSO provider created", createForm.name);
      setCreateOpen(false);
      setCreateForm({ ...EMPTY_FORM });
      await refresh();
    } catch (e: unknown) {
      toast.error("Create failed", e instanceof Error ? e.message : "Unknown error");
    } finally {
      setCreating(false);
    }
  }

  function openEdit(p: SsoProviderConfig) {
    setEditTarget(p);
    setEditForm({
      name: p.name,
      slug: p.slug,
      provider_type: p.provider_type,
      client_id: p.client_id,
      client_secret: "",
      ms_tenant: p.ms_tenant || "",
      auto_provision: p.auto_provision,
      default_role: p.default_role || "member",
      enabled: p.enabled,
      scopes: (p.scopes || []).join(", "),
      authorize_url: p.authorize_url || "",
      token_url: p.token_url || "",
      userinfo_url: p.userinfo_url || "",
      issuer_url: p.issuer_url || "",
    });
  }

  async function handleSaveEdit() {
    if (!editTarget) return;
    setSavingEdit(true);
    try {
      const scopes = editForm.scopes
        ? editForm.scopes.split(",").map((s) => s.trim()).filter(Boolean)
        : ["openid", "email", "profile"];
      const updates: Record<string, unknown> = {
        name: editForm.name,
        provider_type: editForm.provider_type,
        client_id: editForm.client_id,
        ms_tenant: editForm.provider_type === "microsoft" ? editForm.ms_tenant || null : null,
        auto_provision: editForm.auto_provision,
        default_role: editForm.default_role,
        enabled: editForm.enabled,
        scopes,
        authorize_url: editForm.authorize_url || null,
        token_url: editForm.token_url || null,
        userinfo_url: editForm.userinfo_url || null,
        issuer_url: editForm.issuer_url || null,
      };
      if (editForm.client_secret) {
        updates.client_secret = editForm.client_secret;
      }
      await updateSsoProvider(editTarget.id, updates);
      toast.success("SSO provider updated", editForm.name);
      setEditTarget(null);
      await refresh();
    } catch (e: unknown) {
      toast.error("Update failed", e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await deleteSsoProvider(deleteTarget.id);
      toast.success("SSO provider deleted", deleteTarget.name);
      setDeleteTarget(null);
      await refresh();
    } catch (e: unknown) {
      toast.error("Delete failed", e instanceof Error ? e.message : "Unknown error");
    }
  }

  async function handleToggleEnabled(p: SsoProviderConfig) {
    try {
      await updateSsoProvider(p.id, { enabled: !p.enabled });
      toast.success(p.enabled ? "Provider disabled" : "Provider enabled", p.name);
      await refresh();
    } catch (e: unknown) {
      toast.error("Toggle failed", e instanceof Error ? e.message : "Unknown error");
    }
  }

  const isCustomOidc = (t: SsoProviderType) => t === "custom_oidc";

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">SSO Providers</h1>
          <p className="page-subtitle">Configure single sign-on for tenant authentication</p>
        </div>
        {providers.length > 0 && (
          <button className="btn btn-primary" onClick={() => { setCreateForm({ ...EMPTY_FORM }); setCreateOpen(true); }}>
            Add Provider
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading ? (
        <SkeletonTable />
      ) : providers.length === 0 ? (
        <EmptyState
          title="No SSO providers"
          description="Add a Google, Microsoft, or custom OIDC provider to enable single sign-on."
          action={{
            label: "Add Provider",
            onClick: () => { setCreateForm({ ...EMPTY_FORM }); setCreateOpen(true); },
          }}
        />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Provider ID</th>
                <th>Type</th>
                <th>Status</th>
                <th>Auto-Provision</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {providers.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <code style={{ fontSize: "0.75rem" }}>{p.id}</code>
                      <button
                        className="icon-btn"
                        onClick={() => {
                          navigator.clipboard.writeText(p.id);
                          toast.success("Copied", "Provider ID copied to clipboard");
                        }}
                        title="Copy provider ID"
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                        </svg>
                      </button>
                    </div>
                  </td>
                  <td>{providerTypeLabel(p.provider_type)}</td>
                  <td>
                    <button
                      className={`badge-btn ${p.enabled ? "badge-success" : "badge-muted"}`}
                      onClick={() => handleToggleEnabled(p)}
                      title={p.enabled ? "Click to disable" : "Click to enable"}
                    >
                      {p.enabled ? "Enabled" : "Disabled"}
                    </button>
                  </td>
                  <td>{p.auto_provision ? "Yes" : "No"}</td>
                  <td>{p.created_at ? new Date(p.created_at).toLocaleDateString() : "—"}</td>
                  <td>
                    <div className="row-actions">
                      <button className="btn btn-sm" onClick={() => openEdit(p)}>Edit</button>
                      <button className="btn btn-sm btn-danger" onClick={() => setDeleteTarget(p)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Modal */}
      <Modal
        open={createOpen}
        title="Add SSO Provider"
        onClose={() => setCreateOpen(false)}
        footer={
          <>
            <button className="btn" onClick={() => setCreateOpen(false)} disabled={creating}>Cancel</button>
            <button className="btn btn-primary" onClick={handleCreate} disabled={creating || !createForm.name || !createForm.slug || !createForm.client_id}>
              {creating ? "Creating..." : "Create"}
            </button>
          </>
        }
      >
        <ProviderForm
          form={createForm}
          setForm={setCreateForm}
          isCustom={isCustomOidc(createForm.provider_type)}
        />
      </Modal>

      {/* Edit Modal */}
      <Modal
        open={!!editTarget}
        title={`Edit ${editTarget?.name || ""}`}
        onClose={() => setEditTarget(null)}
        footer={
          <>
            <button className="btn" onClick={() => setEditTarget(null)} disabled={savingEdit}>Cancel</button>
            <button className="btn btn-primary" onClick={handleSaveEdit} disabled={savingEdit || !editForm.name || !editForm.client_id}>
              {savingEdit ? "Saving..." : "Save"}
            </button>
          </>
        }
      >
        <ProviderForm
          form={editForm}
          setForm={setEditForm}
          isCustom={isCustomOidc(editForm.provider_type)}
          isEdit
        />
      </Modal>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete SSO Provider"
        description={`Are you sure you want to delete "${deleteTarget?.name}"? Users linked via this provider will lose SSO access (local accounts remain).`}
        confirmText="Delete"
        variant="danger"
        onConfirm={handleDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}

function ProviderForm({
  form,
  setForm,
  isCustom,
  isEdit,
}: {
  form: FormState;
  setForm: React.Dispatch<React.SetStateAction<FormState>>;
  isCustom: boolean;
  isEdit?: boolean;
}) {
  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <div>
      <div className="form-group">
        <label className="form-label">Display Name *</label>
        <input
          type="text"
          value={form.name}
          onChange={(e) => update("name", e.target.value)}
          placeholder="Google"
          required
        />
      </div>
      <div className="form-group">
        <label className="form-label">Slug *</label>
        <input
          type="text"
          value={form.slug}
          onChange={(e) => update("slug", e.target.value)}
          placeholder="google"
          disabled={isEdit}
          required
        />
      </div>
      <div className="form-group">
        <label className="form-label">Provider Type</label>
        <Select
          value={form.provider_type}
          options={PROVIDER_TYPE_OPTIONS}
          onChange={(v) => update("provider_type", v as SsoProviderType)}
        />
      </div>
      <div className="form-group">
        <label className="form-label">Default Role</label>
        <Select
          value={form.default_role}
          options={DEFAULT_ROLE_OPTIONS}
          onChange={(v) => update("default_role", v)}
        />
      </div>
      <div className="form-group">
        <label className="form-label">Client ID *</label>
        <input
          type="text"
          value={form.client_id}
          onChange={(e) => update("client_id", e.target.value)}
          placeholder="your-client-id.apps.googleusercontent.com"
          required
        />
      </div>
      <div className="form-group">
        <label className="form-label">Client Secret {isEdit && <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>(leave blank to keep)</span>}</label>
        <input
          type="password"
          value={form.client_secret}
          onChange={(e) => update("client_secret", e.target.value)}
          placeholder={isEdit ? "••••••••" : "your-client-secret"}
        />
      </div>
      {form.provider_type === "microsoft" && (
        <div className="form-group">
          <label className="form-label">Microsoft Tenant</label>
          <input
            type="text"
            value={form.ms_tenant}
            onChange={(e) => update("ms_tenant", e.target.value)}
            placeholder="common / organizations / <tenant-id>"
          />
        </div>
      )}
      <div className="form-group">
        <label className="form-label">Scopes <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>(comma-separated, defaults to openid,email,profile)</span></label>
        <input
          type="text"
          value={form.scopes}
          onChange={(e) => update("scopes", e.target.value)}
          placeholder="openid, email, profile"
        />
      </div>
      {isCustom && (
        <>
          <div className="form-group">
            <label className="form-label">Authorize URL</label>
            <input
              type="url"
              value={form.authorize_url}
              onChange={(e) => update("authorize_url", e.target.value)}
              placeholder="https://idp.example.com/oauth2/authorize"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Token URL</label>
            <input
              type="url"
              value={form.token_url}
              onChange={(e) => update("token_url", e.target.value)}
              placeholder="https://idp.example.com/oauth2/token"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Userinfo URL</label>
            <input
              type="url"
              value={form.userinfo_url}
              onChange={(e) => update("userinfo_url", e.target.value)}
              placeholder="https://idp.example.com/oauth2/userinfo"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Issuer URL <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>(optional)</span></label>
            <input
              type="url"
              value={form.issuer_url}
              onChange={(e) => update("issuer_url", e.target.value)}
              placeholder="https://idp.example.com"
            />
          </div>
        </>
      )}
      <div className="form-group">
        <label className="form-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={form.auto_provision}
            onChange={(e) => update("auto_provision", e.target.checked)}
          />
          Auto-provision new users on first login
        </label>
      </div>
      <div className="form-group">
        <label className="form-label" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => update("enabled", e.target.checked)}
          />
          Enabled (visible on login page)
        </label>
      </div>
    </div>
  );
}
