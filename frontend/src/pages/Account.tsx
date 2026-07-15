import { useEffect, useState } from "react";
import { fetchMyIdentities, unlinkMyIdentity, UserIdentityInfo } from "../api";
import { useToast } from "../components/Toast";
import { ConfirmDialog } from "../components/ConfirmDialog";

export default function Account() {
  const toast = useToast();
  const [identities, setIdentities] = useState<UserIdentityInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [unlinkTarget, setUnlinkTarget] = useState<UserIdentityInfo | null>(null);

  useEffect(() => {
    loadIdentities();
  }, []);

  async function loadIdentities() {
    setLoading(true);
    try {
      const data = await fetchMyIdentities();
      setIdentities(data);
    } catch (e) {
      toast.error("Failed to load", e instanceof Error ? e.message : "");
    } finally {
      setLoading(false);
    }
  }

  async function handleUnlink() {
    if (!unlinkTarget) return;
    try {
      await unlinkMyIdentity(unlinkTarget.id);
      toast.success("Unlinked", `${unlinkTarget.provider_name} has been removed from your account.`);
      setUnlinkTarget(null);
      loadIdentities();
    } catch (e) {
      toast.error("Unlink failed", e instanceof Error ? e.message : "");
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Account</h1>
        <p className="page-subtitle">Manage your linked SSO identities</p>
      </div>

      <div className="card">
        <div className="card-header">
          <h3 className="card-title">Linked SSO Identities</h3>
        </div>

        {loading ? (
          <div className="empty-state">
            <p className="empty-state-text">Loading...</p>
          </div>
        ) : identities.length === 0 ? (
          <div className="empty-state">
            <p className="empty-state-text">No SSO identities linked.</p>
            <p className="empty-state-subtext">
              Use an SSO provider on the login page to link an identity.
            </p>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Type</th>
                <th>Email</th>
                <th>Linked Since</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {identities.map((id) => (
                <tr key={id.id}>
                  <td>{id.provider_name}</td>
                  <td><code>{id.provider_type}</code></td>
                  <td>{id.email_at_provider || "—"}</td>
                  <td>{id.created_at ? new Date(id.created_at).toLocaleDateString() : "—"}</td>
                  <td>
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => setUnlinkTarget(id)}
                    >
                      Unlink
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <ConfirmDialog
        open={unlinkTarget !== null}
        onClose={() => setUnlinkTarget(null)}
        onConfirm={handleUnlink}
        title={`Unlink ${unlinkTarget?.provider_name || ""}?`}
        description="You will no longer be able to sign in with this provider. This cannot be undone."
        confirmText="Unlink"
        variant="danger"
      />
    </div>
  );
}
