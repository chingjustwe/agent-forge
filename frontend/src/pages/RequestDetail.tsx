import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getObservabilityRequests, getRequestDetail } from "../api";
import TraceTimeline from "../components/TraceTimeline";
import { useWorkspace } from "../context/WorkspaceContext";
import { SkeletonText } from "../components/Skeleton";

interface SpanData {
  span_id: string;
  name: string;
  parent_span_id: string | null;
  duration_ms: number | null;
  attributes: Record<string, unknown>;
}

interface ToolCallData {
  tool_name: string;
  args: string;
  result: string;
  duration_ms: number;
  success: number;
}

interface EventData {
  level: string;
  event: string;
  data: string;
}

interface RequestDetailData {
  request: Record<string, unknown>;
  spans: SpanData[];
  tool_calls: ToolCallData[];
  events: EventData[];
}

export default function RequestDetail() {
  const { currentWorkspaceId } = useWorkspace();
  const { traceId } = useParams<{ traceId: string }>();
  const [detail, setDetail] = useState<RequestDetailData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!currentWorkspaceId || !traceId) return;
    setLoading(true);
    (async () => {
      const requests = await getObservabilityRequests(currentWorkspaceId);
      const found = requests.find(r => r.trace_id === traceId);
      if (found) {
        try {
          const data = await getRequestDetail(currentWorkspaceId, traceId);
          setDetail(data as any);
        } catch {
          // apiFetch handles 401 redirect; other errors ignored
        }
      }
      setLoading(false);
    })();
  }, [currentWorkspaceId, traceId]);

  if (!currentWorkspaceId) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Request Detail</h1>
          <p className="page-subtitle">Trace: {traceId}</p>
        </div>
        <div className="alert alert-info">No workspace selected. Please select a workspace from the top bar.</div>
      </div>
    );
  }

  if (loading) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Request Detail</h1>
          <p className="page-subtitle">Trace: {traceId}</p>
        </div>
        <div className="detail-section">
          <h2 className="detail-section-title">Request Data</h2>
          <SkeletonText lines={8} />
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Request Detail</h1>
          <p className="page-subtitle">Trace: {traceId}</p>
        </div>
        <div className="alert alert-error">Request not found or failed to load.</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Request Detail</h1>
        <p className="page-subtitle">Trace: {traceId}</p>
      </div>

      <div className="detail-section">
        <h2 className="detail-section-title">Request Data</h2>
        <div className="detail-json">
          {JSON.stringify(detail.request, null, 2)}
        </div>
      </div>

      <div className="detail-section">
        <h2 className="detail-section-title">Trace Waterfall</h2>
        <TraceTimeline spans={detail.spans} />
      </div>

      {detail.tool_calls.length > 0 && (
        <div className="detail-section">
          <h2 className="detail-section-title">Tool Calls</h2>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Duration (ms)</th>
                  <th>Success</th>
                </tr>
              </thead>
              <tbody>
                {detail.tool_calls.map((tc, idx) => (
                  <tr key={idx}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{tc.tool_name}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{tc.duration_ms}</td>
                    <td>
                      <span className={`badge ${tc.success ? "badge-success" : "badge-error"}`}>
                        {tc.success ? "Success" : "Failed"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {detail.events.length > 0 && (
        <div className="detail-section">
          <h2 className="detail-section-title">Event Log</h2>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Level</th>
                  <th>Event</th>
                </tr>
              </thead>
              <tbody>
                {detail.events.map((ev, idx) => (
                  <tr key={idx}>
                    <td>
                      <span className={`badge ${
                        ev.level === "error" ? "badge-error" :
                        ev.level === "warn" ? "badge-warning" :
                        "badge-info"
                      }`}>
                        {ev.level}
                      </span>
                    </td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{ev.event}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
