interface Span {
  span_id: string;
  name: string;
  parent_span_id: string | null;
  duration_ms: number | null;
}

export default function TraceTimeline({ spans }: { spans: Span[] }) {
  const maxDur = Math.max(...spans.map(s => s.duration_ms || 0), 1);

  if (spans.length === 0) return null;

  return (
    <div className="trace-timeline">
      {spans.map(span => {
        const pct = maxDur > 0 ? ((span.duration_ms || 0) / maxDur) * 100 : 0;
        return (
          <div key={span.span_id} className="trace-item">
            <div className="trace-name">{span.name}</div>
            <div className="trace-bar-bg">
              <div
                className="trace-bar-fill"
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="trace-duration">
              {span.duration_ms?.toFixed(2)}ms
            </div>
          </div>
        );
      })}
    </div>
  );
}