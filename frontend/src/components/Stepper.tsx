// 分步向导顶部步骤条。
// 两种模式：
//   freeNav=true（编辑）：任意步骤均可点击跳转（自由导航）；已访问步骤显示对勾进度提示。
//   freeNav=false（创建，默认）：仅已完成的步骤（id < current）可点击回跳，未达步骤不可前跳。
// 无障碍：role="tablist" + aria-current。
export interface Step {
  id: number;
  label: string;
}

interface StepperProps {
  steps: Step[];
  current: number;
  onJump: (step: number) => void;
  /** 编辑模式：开启自由导航（所有步骤可点击）。 */
  freeNav?: boolean;
  /** 已访问过的步骤 id 列表（freeNav 模式下用于显示对勾进度提示）。 */
  visited?: number[];
  /** 禁用以阻止点击（如保存中）。 */
  disabled?: boolean;
}

export function Stepper({ steps, current, onJump, freeNav = false, visited = [], disabled = false }: StepperProps) {
  return (
    <div className="stepper" role="tablist" aria-label="Wizard steps">
      {steps.map((step, idx) => {
        const isActive = step.id === current;
        const isCompleted = freeNav
          ? !isActive && visited.includes(step.id)
          : step.id < current;
        const isUpcoming = freeNav
          ? !isActive && !visited.includes(step.id)
          : step.id > current;
        const clickable = !disabled && (freeNav || isCompleted);
        return (
          <div
            key={step.id}
            className={`step${isActive ? " active" : ""}${isCompleted ? " completed" : ""}${isUpcoming ? " upcoming" : ""}`}
            role="tab"
            aria-current={isActive ? "step" : undefined}
            aria-selected={isActive}
            aria-disabled={!clickable}
            tabIndex={clickable ? 0 : -1}
            onClick={() => clickable && onJump(step.id)}
            onKeyDown={(e) => {
              if (clickable && (e.key === "Enter" || e.key === " ")) {
                e.preventDefault();
                onJump(step.id);
              }
            }}
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
