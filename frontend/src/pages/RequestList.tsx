import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getObservabilityRequests, RequestLog } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { EmptyState } from "../components/EmptyState";
import { SkeletonTable } from "../components/Skeleton";

export default function RequestList() {
  const { currentWorkspaceId } = useWorkspace();
  const [requests, setRequests] = useState<RequestLog[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    if (!currentWorkspaceId) return;
    setLoading(true);
    getObservabilityRequests(currentWorkspaceId, { limit: 100 })
      .then(setRequests)
      .finally(() => setLoading(false));
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

        />
      </div>

      {loading ? (
        <SkeletonTable rows={6} cols={5} />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No requests found"
          description={filter ? "Try adjusting your search filter." : "No API requests have been recorded yet."}
        />
      ) : (
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
                  <td style={{ color: r.error ? "var(--color-error)" : "var(--text-muted)" }}>{r.error || "-"}</td>
                  <td style={{ color: "var(--text-secondary)", fontSize: "0.82rem" }}>{r.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
