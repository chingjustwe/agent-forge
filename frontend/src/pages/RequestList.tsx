import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getObservabilityRequests, RequestLog } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";

export default function RequestList() {
  const { currentWorkspaceId } = useWorkspace();
  const [requests, setRequests] = useState<RequestLog[]>([]);
  const [filter, setFilter] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    if (!currentWorkspaceId) return;
    getObservabilityRequests(currentWorkspaceId, { limit: 100 }).then(setRequests);
  }, [currentWorkspaceId]);

  const filtered = requests.filter(r =>
    !filter || r.model?.includes(filter) || r.status_code === Number(filter)
  );

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Request List</h1>
          <p className="page-subtitle">Browse and inspect API requests to your agents</p>
        </div>
        <div className="alert alert-info">No workspace selected. Please select a workspace from the top bar.</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Request List</h1>
        <p className="page-subtitle">Browse and inspect API requests to your agents</p>
      </div>

      <div className="filter-bar">
        <input
          placeholder="Filter by model or status..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{ minWidth: 240 }}
        />
      </div>

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Status</th>
              <th>Duration (ms)</th>
              <th>Error</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(r => (
              <tr key={r.id} onClick={() => navigate(`/requests/${r.trace_id}`)} className="clickable">
                <td>{r.model || "-"}</td>
                <td>
                  <span className={`badge ${r.status_code >= 400 ? "badge-error" : "badge-success"}`}>
                    {r.status_code}
                  </span>
                </td>
                <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{r.duration_ms}</td>
                <td style={{ color: r.error ? "var(--error)" : "var(--text-muted)" }}>{r.error || "-"}</td>
                <td style={{ color: "var(--text-secondary)", fontSize: "0.82rem" }}>{r.created_at}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} style={{ textAlign: "center", padding: 32, color: "var(--text-muted)" }}>
                  No requests found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
