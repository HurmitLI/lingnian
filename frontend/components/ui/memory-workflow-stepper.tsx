const STEPS = [
  { label: "选择回忆", detail: "选一个愿意聊的话题" },
  { label: "留下声音", detail: "录音或上传已有音频" },
  { label: "校对原话", detail: "家人确认转写是否准确" },
  { label: "确认故事", detail: "逐句核对后再归档" },
] as const;

export default function MemoryWorkflowStepper({ currentStep }: { currentStep: number }) {
  return (
    <ol className="memory-stepper" aria-label="记录回忆的四个步骤">
      {STEPS.map((step, index) => {
        const state = index < currentStep ? "complete" : index === currentStep ? "current" : "upcoming";
        return (
          <li key={step.label} data-state={state} aria-current={state === "current" ? "step" : undefined}>
            <span className="memory-step-number" aria-hidden="true">{index < currentStep ? "✓" : index + 1}</span>
            <span className="memory-step-copy">
              <strong>{step.label}</strong>
              <small>{step.detail}</small>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
