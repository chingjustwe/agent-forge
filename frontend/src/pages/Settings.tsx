import { useEffect, useState } from "react";
import { getOtelSettings, updateOtelSettings, OTelConfig } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";

export default function Settings() {
  const { currentWorkspaceId, currentRole } = useWorkspace();
  const [config, setConfig] = useState<OTelConfig>({ enabled: false, endpoint: "", headers: {} });
  const [headersText, setHeadersText] = useState("{}");
  const [saved, setSaved] = useState(false);

  // Workspace-level admin (or tenant_admin) can edit settings.
  const canEdit =
    currentRole === "workspace_admin" ||
    currentRole === "workspace_owner";

  useEffect(() => {
    if (!currentWorkspaceId) return;
    getOtelSettings(currentWorkspaceId).then(data => {
      setConfig(data);
      setHeadersText(JSON.stringify(data.headers, null, 2));
    });
  }, [currentWorkspaceId]);

  const handleSave = async () => {
    if (!currentWorkspaceId) return;
    try {
      const headers = JSON.parse(headersText);
      await updateOtelSettings(currentWorkspaceId, { ...config, headers });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      alert("Invalid JSON in headers");
    }
  };

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">Configure workspace integrations and preferences</p>
        </div>
        <div className="alert alert-info">No workspace selected. Please select a workspace from the top bar.</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Settings</h1>
        <p className="page-subtitle">Configure workspace integrations and preferences</p>
      </div>

      <div className="card settings-section">
        <div className="card-header">
          <h3 className="card-title">OpenTelemetry Export</h3>
        </div>

        <div className="form-group">
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: canEdit ? "pointer" : "default" }}>
            <input
              type="checkbox"
              checked={config.enabled}
              onChange={e => canEdit && setConfig({ ...config, enabled: e.target.checked })}
              disabled={!canEdit}
              style={{ width: "auto" }}
            />
            <span style={{ fontSize: "0.88rem", color: "var(--text-primary)" }}>Enabled</span>
          </label>
        </div>

        <div className="form-group">
          <label className="form-label">Endpoint URL</label>
          <input
            type="text"
            value={config.endpoint}
            onChange={e => canEdit && setConfig({ ...config, endpoint: e.target.value })}
            disabled={!canEdit}
            placeholder="http://otel-collector:4318"
          />
        </div>

        <div className="form-group">
          <label className="form-label">Headers (JSON)</label>
          <textarea
            value={headersText}
            onChange={e => canEdit && setHeadersText(e.target.value)}
            disabled={!canEdit}
            rows={4}
          />
        </div>

        {canEdit && (
          <button className="btn btn-primary" onClick={handleSave}>
            {saved ? "Saved!" : "Save Settings"}
          </button>
        )}
      </div>
    </div>
  );
}
