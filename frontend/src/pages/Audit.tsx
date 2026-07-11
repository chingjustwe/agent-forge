import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getObservabilityRequests, fetchAdminAudit, RequestLog, AuditEntry, AuditResponse } from "../api";
import { useWorkspace } from "../context/WorkspaceContext";
import { EmptyState } from "../components/EmptyState";
import { Select } from "../components/Select";
import { SkeletonTable } from "../components/Skeleton";
import { DatePicker } from "../components/DatePicker";

type AuditTab = "requests" | "logs";

const ACTION_OPTIONS = [
  { value: "", label: "All actions" },
  { value: "workspace.create", label: "workspace.create" },
  { value: "workspace.update", label: "workspace.update" },
  { value: "workspace.delete", label: "workspace.delete" },
  { value: "user.role_change", label: "user.role_change" },
  { value: "user.invite", label: "user.invite" },
  { value: "user.delete", label: "user.delete" },
];

export default function Audit() {
  const { currentWorkspaceId } = useWorkspace();
  const navigate = useNavigate();
  const [tab, setTab] = useState<AuditTab>("requests");

  // ── Requests tab state ──
  const [requests, setRequests] = useState<RequestLog[]>([]);
  const [reqFilter, setReqFilter] = useState("");
  const [reqLoading, setReqLoading] = useState(true);

  // ── Logs tab state ──
  const [auditData, setAuditData] = useState<AuditResponse | null>(null);
  const [auditLoading, setAuditLoading] = useState(true);
  const [action, setAction] = useState("");
  const [userId, setUserId] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const limit = 20;

  // ── Load requests ──
  useEffect(() => {
    if (!currentWorkspaceId || tab !== "requests") return;
    setReqLoading(true);
    getObservabilityRequests(currentWorkspaceId, { limit: 100 })
      .then(setRequests)
      .finally(() => setReqLoading(false));
  }, [currentWorkspaceId, tab]);

  // ── Load audit logs ──
  const loadAudit = () => {
    setAuditLoading(true);
    fetchAdminAudit({
      action: action || undefined,
      user_id: userId || undefined,
      since: since || undefined,
      until: until || undefined,
      limit,
      offset,
    })
      .then(setAuditData)
      .finally(() => setAuditLoading(false));
  };

  useEffect(() => {
    if (tab === "logs") loadAudit();
  }, [tab, offset]); // eslint-disable-line react-hooks/exhaustive-deps

  const filteredRequests = requests.filter(r =>
    !reqFilter || r.model?.includes(reqFilter) || r.status_code === Number(reqFilter)
  );

  const totalPages = auditData ? Math.ceil(auditData.total / limit) : 0;
  const currentPage = Math.floor(offset / limit) + 1;

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Audit</h1>
          <p className="page-subtitle">Request logs and administrative audit trail</p>
        </div>
        <div className="alert alert-info">No workspace selected. Please select a workspace from the top bar.</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Audit</h1>
        <p className="page-subtitle">Request logs and administrative audit trail</p>
      </div>

      {/* Tab switcher */}
      <div className="filter-bar">
        <div className="range-presets">
          <button
            className={`btn btn-sm ${tab === "requests" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setTab("requests")}
          >
            Requests
          </button>
          <button
            className={`btn btn-sm ${tab === "logs" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setTab("logs")}
          >
            Logs
          </button>
        </div>
      </div>

      {/* ── Requests tab ── */}
      {tab === "requests" && (
        <>
          <div className="filter-bar">
            <input
              placeholder="Filter by model or status..."
              value={reqFilter}
              onChange={e => setReqFilter(e.target.value)}
            />
          </div>

          {reqLoading ? (
            <SkeletonTable rows={6} cols={5} />
          ) : filteredRequests.length === 0 ? (
            <EmptyState
              title="No requests found"
              description={reqFilter ? "Try adjusting your search filter." : "No API requests have been recorded yet."}
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
                  {filteredRequests.map(r => (
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
        </>
      )}

      {/* ── Logs tab ── */}
      {tab === "logs" && (
        <>
          <div className="filter-bar">
            <Select
              value={action}
              onChange={setAction}
              options={ACTION_OPTIONS}
            />
            <input placeholder="User ID" value={userId} onChange={(e) => setUserId(e.target.value)} />
            <DatePicker value={since} onChange={setSince} placeholder="From date" max={until || undefined} />
            <DatePicker value={until} onChange={setUntil} placeholder="To date" min={since || undefined} />
            <button className="btn btn-secondary" onClick={loadAudit}>Filter</button>
          </div>

          {auditLoading ? (
            <SkeletonTable rows={8} cols={5} />
          ) : (
            <>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Timestamp</th>
                      <th>User</th>
                      <th>Action</th>
                      <th>Target</th>
                      <th>IP</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditData?.items.length ? (
                      auditData.items.map((entry: AuditEntry) => (
                        <>
                          <tr
                            key={entry.id}
                            onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                            className="clickable"
                          >
                            <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                              {new Date(entry.created_at).toLocaleString()}
                            </td>
                            <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{entry.user_id}</td>
                            <td><span className="badge badge-info">{entry.action}</span></td>
                            <td style={{ fontSize: "0.82rem" }}>{entry.target_type}:{entry.target_id}</td>
                            <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem", color: "var(--text-muted)" }}>
                              {entry.ip_address}
                            </td>
                          </tr>
                          {expandedId === entry.id && (
                            <tr className="expanded-row">
                              <td colSpan={5}>
                                <div className="expanded-content">
                                  <strong>Details</strong>
                                  <pre>{JSON.stringify(entry.details, null, 2)}</pre>
                                </div>
                              </td>
                            </tr>
                          )}
                        </>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={5}>
                          <EmptyState
                            title="No audit entries found"
                            description="Try adjusting your filters or check back later."
                          />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {auditData && auditData.total > 0 && (
                <div className="pagination">
                  <button
                    className="btn btn-secondary btn-sm"
                    disabled={offset <= 0}
                    onClick={() => setOffset(Math.max(0, offset - limit))}
                  >
                    Previous
                  </button>
                  <span className="pagination-info">
                    Page {currentPage} of {totalPages || 1} ({auditData.total} total)
                  </span>
                  <button
                    className="btn btn-secondary btn-sm"
                    disabled={offset + limit >= auditData.total}
                    onClick={() => setOffset(offset + limit)}
                  >
                    Next
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
