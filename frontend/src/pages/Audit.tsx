import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { fetchAdminRequests, fetchAdminAudit, RequestLog, AuditEntry, AuditResponse } from "../api";
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
  const { workspaces } = useWorkspace();
  const navigate = useNavigate();
  const [tab, setTab] = useState<AuditTab>("requests");

  // ── Requests tab state ──
  const [requests, setRequests] = useState<RequestLog[]>([]);
  const [reqLoading, setReqLoading] = useState(true);
  // filters
  const [fWorkspace, setFWorkspace] = useState("");
  const [fUser, setFUser] = useState("");
  const [fAgent, setFAgent] = useState("");
  const [fModel, setFModel] = useState("");
  const [fStatus, setFStatus] = useState("");
  const [fSince, setFSince] = useState("");
  const [fUntil, setFUntil] = useState("");

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

  // ── Load requests (admin cross-workspace) ──
  const loadRequests = useCallback(() => {
    setReqLoading(true);
    fetchAdminRequests({
      workspace_id: fWorkspace || undefined,
      user_id: fUser || undefined,
      agent: fAgent || undefined,
      model: fModel || undefined,
      status: fStatus ? Number(fStatus) : undefined,
      since: fSince || undefined,
      until: fUntil || undefined,
      limit: 100,
    })
      .then(setRequests)
      .finally(() => setReqLoading(false));
  }, [fWorkspace, fUser, fAgent, fModel, fStatus, fSince, fUntil]);

  useEffect(() => {
    if (tab === "requests") loadRequests();
  }, [tab, loadRequests]);

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

  const totalPages = auditData ? Math.ceil(auditData.total / limit) : 0;
  const currentPage = Math.floor(offset / limit) + 1;

  // Workspace options for filter dropdown
  const workspaceOptions = [
    { value: "", label: "All workspaces" },
    ...workspaces.map(w => ({ value: w.id, label: w.name })),
  ];

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
            <Select
              value={fWorkspace}
              onChange={setFWorkspace}
              options={workspaceOptions}
            />
            <input placeholder="User (id or email)" value={fUser} onChange={e => setFUser(e.target.value)} />
            <input placeholder="Agent" value={fAgent} onChange={e => setFAgent(e.target.value)} />
            <input placeholder="Model" value={fModel} onChange={e => setFModel(e.target.value)} />
            <input placeholder="Status" value={fStatus} onChange={e => setFStatus(e.target.value)} style={{ width: 80 }} />
            <DatePicker value={fSince} onChange={setFSince} placeholder="From" max={fUntil || undefined} />
            <DatePicker value={fUntil} onChange={setFUntil} placeholder="To" min={fSince || undefined} />
            <button className="btn btn-secondary" onClick={loadRequests}>Filter</button>
          </div>

          {reqLoading ? (
            <SkeletonTable rows={6} cols={9} />
          ) : requests.length === 0 ? (
            <EmptyState
              title="No requests found"
              description="Try adjusting your filters or check back later."
            />
          ) : (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Workspace</th>
                    <th>User</th>
                    <th>Agent</th>
                    <th>Model</th>
                    <th>Status</th>
                    <th>Duration (ms)</th>
                    <th>Tokens (In/Out)</th>
                    <th>Error</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {requests.map(r => (
                    <tr key={r.id} onClick={() => navigate(`/requests/${r.trace_id}`)} className="clickable">
                      <td style={{ fontSize: "0.82rem" }}>{r.workspace_name || r.workspace_id || "-"}</td>
                      <td style={{ fontSize: "0.82rem" }}>
                        {r.user_name || r.user_email || r.user_id || "-"}
                      </td>
                      <td style={{ fontSize: "0.82rem" }}>{r.agent || "-"}</td>
                      <td>{r.model || "-"}</td>
                      <td>
                        <span className={`badge ${r.status_code >= 400 ? "badge-error" : "badge-success"}`}>
                          {r.status_code}
                        </span>
                      </td>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{r.duration_ms}</td>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                        {(r.input_tokens ?? 0).toLocaleString()} / {(r.output_tokens ?? 0).toLocaleString()}
                      </td>
                      <td style={{ color: r.error ? "var(--color-error)" : "var(--text-muted)", fontSize: "0.82rem", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {r.error || "-"}
                      </td>
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
