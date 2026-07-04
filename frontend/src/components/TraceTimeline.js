import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export default function TraceTimeline({ spans }) {
    const maxDur = Math.max(...spans.map(s => s.duration_ms || 0), 1);
    if (spans.length === 0)
        return null;
    return (_jsx("div", { className: "trace-timeline", children: spans.map(span => {
            const pct = maxDur > 0 ? ((span.duration_ms || 0) / maxDur) * 100 : 0;
            return (_jsxs("div", { className: "trace-item", children: [_jsx("div", { className: "trace-name", children: span.name }), _jsx("div", { className: "trace-bar-bg", children: _jsx("div", { className: "trace-bar-fill", style: { width: `${pct}%` } }) }), _jsxs("div", { className: "trace-duration", children: [span.duration_ms?.toFixed(2), "ms"] })] }, span.span_id));
        }) }));
}
