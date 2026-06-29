import { useEffect, useState } from "react";
import { getOtelSettings, updateOtelSettings, OTelConfig } from "../api";

export default function Settings({ wsId, isAdmin }: { wsId: string; isAdmin: boolean }) {
  const [config, setConfig] = useState<OTelConfig>({ enabled: false, endpoint: "", headers: {} });
  const [headersText, setHeadersText] = useState("{}");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getOtelSettings(wsId).then(data => {
      setConfig(data);
      setHeadersText(JSON.stringify(data.headers, null, 2));
    });
  }, [wsId]);

  const handleSave = async () => {
    try {
      const headers = JSON.parse(headersText);
      await updateOtelSettings(wsId, { ...config, headers });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      alert("Invalid JSON in headers");
    }
  };

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
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: isAdmin ? "pointer" : "default" }}>
            <input
              type="checkbox"
              checked={config.enabled}
              onChange={e => isAdmin && setConfig({ ...config, enabled: e.target.checked })}
              disabled={!isAdmin}
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
            onChange={e => isAdmin && setConfig({ ...config, endpoint: e.target.value })}
            disabled={!isAdmin}
            placeholder="http://otel-collector:4318"
          />
        </div>

        <div className="form-group">
          <label className="form-label">Headers (JSON)</label>
          <textarea
            value={headersText}
            onChange={e => isAdmin && setHeadersText(e.target.value)}
            disabled={!isAdmin}
            rows={4}
          />
        </div>

        {isAdmin && (
          <button className="btn btn-primary" onClick={handleSave}>
            {saved ? "Saved!" : "Save Settings"}
          </button>
        )}
      </div>
    </div>
  );
}