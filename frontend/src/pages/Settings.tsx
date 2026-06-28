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
    <div style={{ padding: 24 }}>
      <h1>Settings</h1>

      <div style={{ background: "#fff", padding: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)", maxWidth: 500 }}>
        <h3>OpenTelemetry Export</h3>

        <div style={{ marginBottom: 12 }}>
          <label>
            <input type="checkbox" checked={config.enabled}
              onChange={e => isAdmin && setConfig({ ...config, enabled: e.target.checked })}
              disabled={!isAdmin} />
            {" "}Enabled
          </label>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label>Endpoint URL:</label>
          <input
            type="text"
            value={config.endpoint}
            onChange={e => isAdmin && setConfig({ ...config, endpoint: e.target.value })}
            disabled={!isAdmin}
            style={{ width: "100%", padding: 8, marginTop: 4 }}
            placeholder="http://otel-collector:4318"
          />
        </div>

        <div style={{ marginBottom: 12 }}>
          <label>Headers (JSON):</label>
          <textarea
            value={headersText}
            onChange={e => isAdmin && setHeadersText(e.target.value)}
            disabled={!isAdmin}
            rows={4}
            style={{ width: "100%", padding: 8, marginTop: 4, fontFamily: "monospace" }}
          />
        </div>

        {isAdmin && (
          <button onClick={handleSave} style={{ padding: "8px 16px" }}>
            {saved ? "Saved!" : "Save Settings"}
          </button>
        )}
      </div>
    </div>
  );
}
