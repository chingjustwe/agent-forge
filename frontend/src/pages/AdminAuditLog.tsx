import { useEffect, useState } from "react";
import { fetchAdminAudit, AuditEntry, AuditResponse } from "../api";
import { EmptyState } from "../components/EmptyState";
import { SkeletonTable } from "../components/Skeleton";

const ACTIONS = [
  "",
  "workspace.create",
  "workspace.update",
  "workspace.delete",
  "user.role_change",
  "user.invite",
  "user.delete",
];

export default function AdminAuditLog() {
  const [data, setData] = useState<AuditResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState("");
  const [userId, setUserId] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const limit = 20;

  const load = () => {
    setLoading(true);
    fetchAdminAudit({
      action: action || undefined,
      user_id: userId || undefined,
      since: since || undefined,
      until: until || undefined,
      limit,
      offset,
    })
      .then(setData)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [offset]);

  const totalPages = data ? Math.ceil(data.total / limit) : 0;
  const currentPage = Math.floor(offset / limit) + 1;

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Audit Log</h1>
        <p className="page-subtitle">Track administrative actions across the platform</p>
      </div>

      <div className="filter-bar">
        <select value={action} onChange={(e) => setAction(e.target.value)}>
          {ACTIONS.map((a) => (
            <option key={a} value={a}>{a || "All actions"}</option>
          ))}
        </select>
        <input placeholder="User ID" value={userId} onChange={(e) => setUserId(e.target.value)} style={{ minWidth: 200 }} />
        <input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
        <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        <button className="btn btn-secondary" onClick={load}>Filter</button>
      </div>

      {loading ? (
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
                {data?.items.length ? (
                  data.items.map((entry: AuditEntry) => (
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

          {data && (
            <div className="pagination">
              <button
                className="btn btn-secondary btn-sm"
                disabled={offset <= 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}
              >
                Previous
              </button>
              <span className="pagination-info">
                Page {currentPage} of {totalPages || 1} ({data.total} total)
              </span>
              <button
                className="btn btn-secondary btn-sm"
                disabled={offset + limit >= data.total}
                onClick={() => setOffset(offset + limit)}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
