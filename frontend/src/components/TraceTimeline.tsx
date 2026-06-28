interface Span {
  span_id: string;
  name: string;
  parent_span_id: string | null;
  duration_ms: number | null;
}

export default function TraceTimeline({ spans }: { spans: Span[] }) {
  const maxDur = Math.max(...spans.map(s => s.duration_ms || 0), 1);

  return (
    <div style={{ background: "#fff", padding: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,0.1)", marginBottom: 16 }}>
      {spans.map(span => {
        const pct = maxDur > 0 ? ((span.duration_ms || 0) / maxDur) * 100 : 0;
        return (
          <div key={span.span_id} style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
            <div style={{ width: 180, fontSize: 13, color: "#333" }}>{span.name}</div>
            <div style={{ flex: 1, background: "#eee", borderRadius: 4, height: 20, position: "relative" }}>
              <div
                style={{
                  width: `${pct}%`,
                  background: "#0088FE",
                  borderRadius: 4,
                  height: 20,
                  transition: "width 0.3s",
                }}
              />
            </div>
            <div style={{ width: 80, textAlign: "right", fontSize: 12, color: "#666", marginLeft: 8 }}>
              {span.duration_ms?.toFixed(2)}ms
            </div>
          </div>
        );
      })}
    </div>
  );
}
