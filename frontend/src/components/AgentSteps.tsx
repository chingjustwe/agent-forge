import { AgentStep } from "../api";

interface AgentStepsProps {
  steps: AgentStep[];
  streaming: boolean;
}

export function AgentSteps({ steps, streaming }: AgentStepsProps) {
  if (!steps.length) return null;
  return (
    <div className="agent-steps">
      {steps.map((step, i) =>
        step.kind === "reasoning" ? (
          <ReasoningBlock key={`r-${i}`} step={step} streaming={streaming && i === steps.length - 1} />
        ) : (
          <ToolBlock key={`t-${step.id || i}`} step={step} />
        )
      )}
    </div>
  );
}

function ReasoningBlock({ step, streaming }: { step: Extract<AgentStep, { kind: "reasoning" }>; streaming: boolean }) {
  return (
    <details className="agent-step" open={streaming}>
      <summary>
        <span className="agent-step-dot reasoning" />
        <span>{streaming ? "Thinking..." : "Thinking"}</span>
        <svg className="agent-step-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3 4.5L6 7.5L9 4.5" />
        </svg>
      </summary>
      <div className="agent-step-body">
        <div className="agent-step-thinking">{step.content}</div>
      </div>
    </details>
  );
}

function ToolBlock({ step }: { step: Extract<AgentStep, { kind: "tool" }> }) {
  const isRunning = step.status === "running";
  return (
    <details className="agent-step" open={isRunning}>
      <summary>
        <span className={`agent-step-dot ${isRunning ? "running" : "done"}`} />
        <span className="agent-step-tool-name">{step.name}</span>
        <svg className="agent-step-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3 4.5L6 7.5L9 4.5" />
        </svg>
      </summary>
      <div className="agent-step-body">
        {step.args ? (
          <div className="agent-step-section">
            <div className="agent-step-label">Arguments</div>
            <pre>{typeof step.args === "string" ? step.args : JSON.stringify(step.args, null, 2)}</pre>
          </div>
        ) : null}
        {step.result != null && (
          <div className="agent-step-section">
            <div className="agent-step-label">Result</div>
            <pre className={step.error ? "agent-step-error" : ""}>{step.error || step.result}</pre>
          </div>
        )}
      </div>
    </details>
  );
}
