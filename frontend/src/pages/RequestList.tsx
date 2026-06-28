import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getObservabilityRequests, RequestLog } from "../api";

export default function RequestList({ wsId }: { wsId: string }) {
  const [requests, setRequests] = useState<RequestLog[]>([]);
  const [filter, setFilter] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    getObservabilityRequests(wsId, { limit: 100 }).then(setRequests);
  }, [wsId]);

  const filtered = requests.filter(r =>
    !filter || r.model?.includes(filter) || r.status_code === Number(filter)
  );

  return (
    <div style={{ padding: 24 }}>
      <h1>Request List</h1>
      <input
        placeholder="Filter by model or status..."
        value={filter}
        onChange={e => setFilter(e.target.value)}
        style={{ marginBottom: 16, padding: 8, width: 300 }}
      />
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ background: "#f5f5f5" }}>
            <th style={thStyle}>Model</th>
            <th style={thStyle}>Status</th>
            <th style={thStyle}>Duration (ms)</th>
            <th style={thStyle}>Error</th>
            <th style={thStyle}>Created</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(r => (
            <tr key={r.id} onClick={() => navigate(`/requests/${r.trace_id}`)} style={{ cursor: "pointer" }}>
              <td style={tdStyle}>{r.model || "-"}</td>
              <td style={tdStyle}>{r.status_code}</td>
              <td style={tdStyle}>{r.duration_ms}</td>
              <td style={tdStyle}>{r.error || "-"}</td>
              <td style={tdStyle}>{r.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const thStyle: React.CSSProperties = { padding: 8, borderBottom: "2px solid #ddd", textAlign: "left" };
const tdStyle: React.CSSProperties = { padding: 8, borderBottom: "1px solid #eee" };
