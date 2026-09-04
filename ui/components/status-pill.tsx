import type { WorkflowState } from "@/lib/contracts";
import { sentenceCase } from "@/lib/format";

const tones: Record<WorkflowState, string> = {
  AWAITING_CONFIRMATION: "attention",
  CONFIRMED: "processing",
  SUCCEEDED: "success",
  EXECUTION_FAILED: "danger",
  CANCELLED: "neutral",
  EXPIRED: "neutral",
  STALE: "danger",
  SUPERSEDED: "neutral",
};

export function StatusPill({ state }: { state: WorkflowState }) {
  return (
    <span className="status-pill" data-tone={tones[state]}>
      <span aria-hidden="true" />
      {sentenceCase(state)}
    </span>
  );
}
