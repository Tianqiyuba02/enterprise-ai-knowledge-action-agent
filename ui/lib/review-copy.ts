import type { WorkflowState } from "@/lib/contracts";

type ReviewKind = "leave" | "it";

export function reviewStatusCopy(state: WorkflowState, kind: ReviewKind): string {
  const item = kind === "leave" ? "leave request" : "IT support request";
  if (state === "AWAITING_CONFIRMATION") {
    return "This is a draft — nothing has been submitted yet.";
  }
  if (state === "CONFIRMED") {
    return `This ${item} is authorized and waiting for deterministic processing.`;
  }
  if (state === "SUCCEEDED") {
    return kind === "leave"
      ? "This leave request was submitted successfully."
      : "This IT support request created a ticket successfully.";
  }
  if (state === "EXECUTION_FAILED") {
    return `This ${item} could not be completed. Nothing was submitted.`;
  }
  if (state === "STALE") {
    return "This draft is no longer current and cannot be submitted.";
  }
  if (state === "EXPIRED") {
    return "This draft expired and cannot be submitted.";
  }
  if (state === "CANCELLED") {
    return "This draft was cancelled. Nothing was submitted.";
  }
  return "This revision was replaced by a newer draft.";
}
