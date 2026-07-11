// 分步向导顶部步骤条。
// 状态：completed（对勾，可点击回跳）/ active（高亮+序号）/ upcoming（灰）。
// 交互：已完成的步骤可点击回跳；当前步与未达步骤不可向前跳（防止跳过必填）。
// 无障碍：role="tablist" + aria-current。
export interface Step {
  id: number;
  label: string;
}

interface StepperProps {
  steps: Step[];
  current: number;
  onJump: (step: number) => void;
}

export function Stepper({ steps, current, onJump }: StepperProps) {
  return (
    <div className="stepper" role="tablist" aria-label="Wizard steps">
      {steps.map((step, idx) => {
        const isCompleted = step.id < current;
        const isActive = step.id === current;
        const isUpcoming = step.id > current;
        const clickable = isCompleted;
        return (
          <div
            key={step.id}
            className={`step${isActive ? " active" : ""}${isCompleted ? " completed" : ""}${isUpcoming ? " upcoming" : ""}`}
            role="tab"
            aria-current={isActive ? "step" : undefined}
            aria-selected={isActive}
            aria-disabled={!clickable}
            onClick={() => clickable && onJump(step.id)}
            style={{ cursor: clickable ? "pointer" : "default" }}
          >
            <div className="step-num">
              {isCompleted ? (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M3 8.5L6.5 12L13 4.5"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              ) : (
                idx + 1
              )}
            </div>
            <span className="step-label">{step.label}</span>
            {idx < steps.length - 1 && <div className="step-line" />}
          </div>
        );
      })}
    </div>
  );
}
