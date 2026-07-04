import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getObservabilityRequests, getRequestDetail } from "../api";
import TraceTimeline from "../components/TraceTimeline";
import { useWorkspace } from "../context/WorkspaceContext";
export default function RequestDetail() {
    const { currentWorkspaceId } = useWorkspace();
    const { traceId } = useParams();
    const [detail, setDetail] = useState(null);
    useEffect(() => {
        if (!currentWorkspaceId || !traceId)
            return;
        (async () => {
            const requests = await getObservabilityRequests(currentWorkspaceId);
            const found = requests.find(r => r.trace_id === traceId);
            if (found) {
                try {
                    const data = await getRequestDetail(currentWorkspaceId, traceId);
                    setDetail(data);
                }
                catch {
                    // apiFetch handles 401 redirect; other errors ignored
                }
            }
        })();
    }, [currentWorkspaceId, traceId]);
    if (!currentWorkspaceId) {
        return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Request Detail" }), _jsxs("p", { className: "page-subtitle", children: ["Trace: ", traceId] })] }), _jsx("div", { className: "alert alert-info", children: "No workspace selected. Please select a workspace from the top bar." })] }));
    }
    if (!detail)
        return _jsx("div", { className: "loading", children: "Loading request details" });
    return (_jsxs("div", { children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { className: "page-title", children: "Request Detail" }), _jsxs("p", { className: "page-subtitle", children: ["Trace: ", traceId] })] }), _jsxs("div", { className: "detail-section", children: [_jsx("h2", { className: "detail-section-title", children: "Request Data" }), _jsx("div", { className: "detail-json", children: JSON.stringify(detail.request, null, 2) })] }), _jsxs("div", { className: "detail-section", children: [_jsx("h2", { className: "detail-section-title", children: "Trace Waterfall" }), _jsx(TraceTimeline, { spans: detail.spans })] }), detail.tool_calls.length > 0 && (_jsxs("div", { className: "detail-section", children: [_jsx("h2", { className: "detail-section-title", children: "Tool Calls" }), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Tool" }), _jsx("th", { children: "Duration (ms)" }), _jsx("th", { children: "Success" })] }) }), _jsx("tbody", { children: detail.tool_calls.map((tc, idx) => (_jsxs("tr", { children: [_jsx("td", { style: { fontFamily: "var(--font-mono)", fontSize: "0.82rem" }, children: tc.tool_name }), _jsx("td", { style: { fontFamily: "var(--font-mono)", fontSize: "0.82rem" }, children: tc.duration_ms }), _jsx("td", { children: _jsx("span", { className: `badge ${tc.success ? "badge-success" : "badge-error"}`, children: tc.success ? "Success" : "Failed" }) })] }, idx))) })] }) })] })), detail.events.length > 0 && (_jsxs("div", { className: "detail-section", children: [_jsx("h2", { className: "detail-section-title", children: "Event Log" }), _jsx("div", { className: "table-container", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Level" }), _jsx("th", { children: "Event" })] }) }), _jsx("tbody", { children: detail.events.map((ev, idx) => (_jsxs("tr", { children: [_jsx("td", { children: _jsx("span", { className: `badge ${ev.level === "error" ? "badge-error" :
                                                        ev.level === "warn" ? "badge-warning" :
                                                            "badge-info"}`, children: ev.level }) }), _jsx("td", { style: { fontFamily: "var(--font-mono)", fontSize: "0.82rem" }, children: ev.event })] }, idx))) })] }) })] }))] }));
}
