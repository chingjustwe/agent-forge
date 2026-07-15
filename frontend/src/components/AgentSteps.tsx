import { useState } from "react";
import { AgentStep, resolveToolApproval } from "../api";

interface AgentStepsProps {
  steps: AgentStep[];
  streaming: boolean;
  /** Final text content — rendered as the last timeline node. */
  content?: string;
  /** Current session id — required for HITL tool approval actions. */
  sessionId?: string;
}

/**
 * Codex-style timeline: reasoning → tool calls → final response.
 * Left rail connects all steps vertically; the active step is expanded
 * with a pulse animation; completed steps collapse to a single line.
 */
export function AgentSteps({ steps, streaming, content, sessionId }: AgentStepsProps) {
  // Determine which step is "active" (last one, if streaming).
  const activeIdx = streaming ? steps.length - 1 : -1;

  // Whether the final text response is still streaming.
  const textStreaming = streaming && steps.length > 0 && steps[steps.length - 1].kind !== "reasoning";

  return (
    <div className="react-timeline">
      {steps.map((step, i) =>
        step.kind === "reasoning" ? (
          <ReasoningNode
            key={`r-${i}`}
            step={step}
            active={i === activeIdx}
            isLast={i === steps.length - 1}
          />
        ) : step.kind === "truncated" ? (
          <TruncatedNode
            key={`c-${i}`}
            isLast={i === steps.length - 1}
          />
        ) : step.kind === "tool_approval" ? (
          <ToolApprovalNode
            key={`a-${step.id || i}`}
            step={step}
            sessionId={sessionId}
            isLast={i === steps.length - 1}
          />
        ) : (
          <ToolNode
            key={`t-${step.id || i}`}
            step={step}
            active={i === activeIdx}
            isLast={i === steps.length - 1}
          />
        )
      )}
      {content && (
        <div className="timeline-node timeline-node-response">
          <div className="timeline-rail" />
          <div className="timeline-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 6h16M4 12h16M4 18h10" />
            </svg>
          </div>
          <div className="timeline-content">
            <div className="timeline-label">
              {textStreaming ? "Response" : "Response"}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ReasoningNode({
  step,
  active,
  isLast,
}: {
  step: Extract<AgentStep, { kind: "reasoning" }>;
  active: boolean;
  isLast: boolean;
}) {
  // Auto-expand while active (streaming). Once done, collapse by default.
  const [open, setOpen] = useState(active);
  // Sync: when active turns false (stream moved on), collapse.
  if (!active && open && step.content.length > 200) {
    // Will collapse on next render via user interaction; for now keep state.
  }

  const preview = step.content.slice(0, 120);
  const truncated = step.content.length > 120;

  return (
    <div className={`timeline-node timeline-node-reasoning ${active ? "active" : ""}`}>
      <div className={`timeline-rail ${isLast && !active ? "timeline-rail-end" : ""}`} />
      <div className="timeline-icon timeline-icon-reasoning">
        {active ? (
          <span className="timeline-pulse" />
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.5.4.8 1 .8 1.6V18h6.4v-1.7c0-.6.3-1.2.8-1.6A7 7 0 0 0 12 2z" />
          </svg>
        )}
      </div>
      <div className="timeline-content">
        <div className="timeline-header" onClick={() => setOpen(!open)}>
          <span className="timeline-label">
            {active ? "Thinking..." : "Thought"}
          </span>
          {!active && !open && truncated && (
            <span className="timeline-preview">{preview}…</span>
          )}
          <svg
            className={`timeline-chevron ${open ? "expanded" : ""}`}
            width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"
          >
            <path d="M3 4.5L6 7.5L9 4.5" />
          </svg>
        </div>
        {open && (
          <div className="timeline-body timeline-body-reasoning">
            {step.content}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolNode({
  step,
  active,
  isLast,
}: {
  step: Extract<AgentStep, { kind: "tool" }>;
  active: boolean;
  isLast: boolean;
}) {
  const isRunning = step.status === "running";
  // Auto-expand while running; collapse when done.
  const [open, setOpen] = useState(isRunning);

  const argsStr = step.args
    ? typeof step.args === "string"
      ? step.args
      : JSON.stringify(step.args, null, 2)
    : "";

  return (
    <div className={`timeline-node timeline-node-tool ${isRunning ? "running" : "done"}`}>
      <div className={`timeline-rail ${isLast && !isRunning ? "timeline-rail-end" : ""}`} />
      <div className={`timeline-icon ${isRunning ? "timeline-icon-running" : "timeline-icon-done"}`}>
        {isRunning ? (
          <span className="timeline-pulse" />
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M14.7 6.3a4 4 0 0 1-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 0 0 5.4-5.4l-2.1 2.1-2.4-2.4 2.5-1.7z" />
          </svg>
        )}
      </div>
      <div className="timeline-content">
        <div className="timeline-header" onClick={() => setOpen(!open)}>
          <span className="timeline-label timeline-tool-name">{step.name}</span>
          {isRunning && <span className="timeline-badge timeline-badge-running">running</span>}
          {!isRunning && step.error && <span className="timeline-badge timeline-badge-error">error</span>}
          <svg
            className={`timeline-chevron ${open ? "expanded" : ""}`}
            width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"
          >
            <path d="M3 4.5L6 7.5L9 4.5" />
          </svg>
        </div>
        {open && (
          <div className="timeline-body">
            {argsStr && (
              <div className="timeline-section">
                <div className="timeline-section-label">Arguments</div>
                <pre className="timeline-pre">{argsStr}</pre>
              </div>
            )}
            {step.result != null && (
              <div className="timeline-section">
                <div className="timeline-section-label">Result</div>
                <pre className={`timeline-pre ${step.error ? "timeline-pre-error" : ""}`}>
                  {step.error || step.result}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function TruncatedNode({ isLast }: { isLast: boolean }) {
  return (
    <div
      className="timeline-node timeline-node-truncated"
      role="alert"
      aria-live="polite"
    >
      <div className={`timeline-rail ${isLast ? "timeline-rail-end" : ""}`} />
      <div className="timeline-icon timeline-icon-truncated">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="6" cy="6" r="3" />
          <circle cx="6" cy="18" r="3" />
          <line x1="20" y1="4" x2="8.12" y2="15.88" />
          <line x1="14.47" y1="14.48" x2="20" y2="20" />
          <line x1="8.12" y1="8.12" x2="12" y2="12" />
        </svg>
      </div>
      <div className="timeline-content">
        <div className="timeline-label timeline-label-truncated">
          Response truncated
        </div>
        <div className="timeline-truncated-body">
          The reply was cut off because it hit the agent&apos;s max_tokens limit.
          Increase max_tokens in the agent settings to allow longer responses.
        </div>
      </div>
    </div>
  );
}

function ToolApprovalNode({
  step,
  sessionId,
  isLast,
}: {
  step: Extract<AgentStep, { kind: "tool_approval" }>;
  sessionId?: string;
  isLast: boolean;
}) {
  const isAwaiting = step.status === "awaiting_approval";
  const [open, setOpen] = useState(isAwaiting);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const argsStr = step.args
    ? typeof step.args === "string"
      ? step.args
      : JSON.stringify(step.args, null, 2)
    : "";

  async function decide(approved: boolean, alwaysAllow: boolean) {
    if (!sessionId || !isAwaiting || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await resolveToolApproval(sessionId, step.id, {
        approved,
        always_allow: alwaysAllow,
      });
      // The backend resolves the future → shim proceeds → a tool_result
      // SSE event arrives and flips the step status to "resolved" via
      // the Sessions.tsx handler. No local state update needed here.
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  return (
    <div
      className={`timeline-node timeline-node-approval ${isAwaiting ? "pending" : "resolved"}`}
      role="alert"
      aria-live="polite"
    >
      <div className={`timeline-rail ${isLast && !isAwaiting ? "timeline-rail-end" : ""}`} />
      <div className={`timeline-icon ${isAwaiting ? "timeline-icon-approval-pending" : "timeline-icon-approval-resolved"}`}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z" />
          <path d="M12 8v4" />
          <path d="M12 16h.01" />
        </svg>
      </div>
      <div className="timeline-content">
        <div className="timeline-header" onClick={() => setOpen(!open)}>
          <span className="timeline-label timeline-tool-name">{step.name}</span>
          {isAwaiting && (
            <span className="timeline-badge timeline-badge-approval">approval required</span>
          )}
          {!isAwaiting && (
            <span className="timeline-badge timeline-badge-resolved">resolved</span>
          )}
          <svg
            className={`timeline-chevron ${open ? "expanded" : ""}`}
            width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"
          >
            <path d="M3 4.5L6 7.5L9 4.5" />
          </svg>
        </div>
        {open && (
          <div className="timeline-body">
            {step.reason && (
              <div className="timeline-section">
                <div className="timeline-section-label">Reason</div>
                <div className="timeline-approval-reason">{step.reason}</div>
              </div>
            )}
            {argsStr && (
              <div className="timeline-section">
                <div className="timeline-section-label">Arguments</div>
                <pre className="timeline-pre">{argsStr}</pre>
              </div>
            )}
            {isAwaiting && (
              <div className="timeline-approval-actions">
                <button
                  type="button"
                  className="btn btn-approve"
                  disabled={submitting || !sessionId}
                  onClick={() => decide(true, false)}
                >
                  Approve once
                </button>
                <button
                  type="button"
                  className="btn btn-approve-always"
                  disabled={submitting || !sessionId}
                  onClick={() => decide(true, true)}
                >
                  Always allow this session
                </button>
                <button
                  type="button"
                  className="btn btn-deny"
                  disabled={submitting || !sessionId}
                  onClick={() => decide(false, false)}
                >
                  Deny
                </button>
              </div>
            )}
            {!isAwaiting && step.result != null && (
              <div className="timeline-section">
                <div className="timeline-section-label">Result</div>
                <pre className={`timeline-pre ${step.error ? "timeline-pre-error" : ""}`}>
                  {step.error || step.result}
                </pre>
              </div>
            )}
            {error && (
              <div className="timeline-approval-error">{error}</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
