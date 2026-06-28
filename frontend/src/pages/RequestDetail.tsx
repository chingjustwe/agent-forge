import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getObservabilityRequests } from "../api";
import TraceTimeline from "../components/TraceTimeline";

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

export default function RequestDetail({ wsId }: { wsId: string }) {
  const { traceId } = useParams<{ traceId: string }>();
  const [detail, setDetail] = useState<RequestDetailData | null>(null);

  useEffect(() => {
    if (!traceId) return;
    (async () => {
      const requests = await getObservabilityRequests(wsId);
      const found = requests.find(r => r.trace_id === traceId);
      if (found) {
        const resp = await fetch(`/api/v1/workspaces/${wsId}/observability/requests/${traceId}`, {
          headers: { Authorization: `Bearer ${localStorage.getItem("agent_platform_token")}` },
        });
        if (resp.ok) setDetail(await resp.json());
      }
    })();
  }, [wsId, traceId]);

  if (!detail) return <div style={{ padding: 24 }}>Loading...</div>;

  return (
    <div style={{ padding: 24 }}>
      <h1>Request Detail</h1>
      <pre style={{ background: "#f5f5f5", padding: 16, borderRadius: 8 }}>
        {JSON.stringify(detail.request, null, 2)}
      </pre>

      <h2>Trace Waterfall</h2>
      <TraceTimeline spans={detail.spans} />

      {detail.tool_calls.length > 0 && (
        <>
          <h2>Tool Calls</h2>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "#f5f5f5" }}>
                <th style={thStyle}>Tool</th>
                <th style={thStyle}>Duration (ms)</th>
                <th style={thStyle}>Success</th>
              </tr>
            </thead>
            <tbody>
              {detail.tool_calls.map((tc, idx) => (
                <tr key={idx}>
                  <td style={tdStyle}>{tc.tool_name}</td>
                  <td style={tdStyle}>{tc.duration_ms}</td>
                  <td style={tdStyle}>{tc.success ? "✓" : "✗"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {detail.events.length > 0 && (
        <>
          <h2>Event Log</h2>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "#f5f5f5" }}>
                <th style={thStyle}>Level</th>
                <th style={thStyle}>Event</th>
              </tr>
            </thead>
            <tbody>
              {detail.events.map((ev, idx) => (
                <tr key={idx}>
                  <td style={tdStyle}>{ev.level}</td>
                  <td style={tdStyle}>{ev.event}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

const thStyle: React.CSSProperties = { padding: 8, borderBottom: "2px solid #ddd", textAlign: "left" };
const tdStyle: React.CSSProperties = { padding: 8, borderBottom: "1px solid #eee" };
