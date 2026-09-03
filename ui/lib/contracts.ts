export type PersonaId = "alex" | "sam";

export type DemoReadiness = {
  status: "ready" | "degraded" | "maintenance";
  database: boolean;
  migration: boolean;
  knowledge: boolean;
  maintenance: boolean;
  worker: boolean;
  worker_heartbeat_at: string | null;
  last_successful_reset_at: string | null;
  document_count: number;
  chunk_count: number;
};

export type GuidedScenario = {
  id: string;
  label: string;
  prompt: string;
  available: boolean;
  note: string | null;
};

export type GuidedScenarios = { items: GuidedScenario[] };

export type EmployeeProfile = {
  employee_id: string;
  full_name: string;
  work_email: string;
  location: string;
  employment_type: string;
  hours_per_day: number;
  work_days: string[];
  timezone: string;
  is_active: boolean;
};

export type WorkflowState =
  | "AWAITING_CONFIRMATION"
  | "CONFIRMED"
  | "SUCCEEDED"
  | "EXECUTION_FAILED"
  | "CANCELLED"
  | "EXPIRED"
  | "STALE"
  | "SUPERSEDED";

export type StableAuthority = {
  employee_id: string;
  jurisdiction: string;
  work_days: string[];
  hours_per_day: string;
  timezone: string;
  calendar_version: string;
  ruleset_version: string;
};

export type AnnualLeaveDraft = {
  action_type: "submit_annual_leave";
  leave_type: "annual";
  start_date: string;
  end_date: string;
  requested_hours: string;
  projected_balance_hours: string;
  readiness: string;
  reason: string | null;
  calendar_version: string;
  ruleset_version: string;
  authority_snapshot_hash: string;
  scheduled_work_days: number;
  stable_authority: StableAuthority;
};

export type ITTicketCategory = "access" | "hardware" | "software" | "network";
export type ITTicketUrgency = "low" | "medium" | "high";
export type ITTicketStatus = "open" | "in_progress" | "resolved";

export type ITSupportTicketDraft = {
  action_type: "create_it_support_ticket";
  category: ITTicketCategory;
  summary: string;
  description: string;
  urgency: ITTicketUrgency;
  ruleset_version: "it-support-v1";
  authority_snapshot_hash: string;
};

export type AuthoritativeDraft = AnnualLeaveDraft | ITSupportTicketDraft;

export type LeaveRequestResult = {
  leave_request_id: string;
  source_action_id: string;
  leave_type: "annual";
  start_date: string;
  end_date: string;
  requested_hours: string;
  reason: string | null;
  status: "submitted";
  submitted_at: string;
  calendar_version: string;
  ruleset_version: string;
};

export type LeaveBalance = {
  leave_type: "annual" | "personal";
  base_balance_hours: string;
  committed_hours: string;
  available_hours: string;
  source_as_of_date: string;
};

export type LeaveSummary = {
  balances: LeaveBalance[];
  requests: LeaveRequestResult[];
  computed_at: string;
};

export type AnnualLeaveActionListItem = {
  action_id: string;
  revision: number;
  action_type: "submit_annual_leave";
  state: WorkflowState;
  start_date: string;
  end_date: string;
  requested_hours: string;
  reason: string | null;
  created_at: string;
  updated_at: string;
  action_expires_at: string;
  confirmed_expires_at: string | null;
  confirmation_required: boolean;
  result: LeaveRequestResult | null;
};

export type ITTicketResult = {
  ticket_id: string;
  category: ITTicketCategory;
  summary: string;
  urgency: ITTicketUrgency;
  status: ITTicketStatus;
  created_at: string;
  updated_at: string;
};

export type ITActionListItem = {
  action_id: string;
  revision: number;
  action_type: "create_it_support_ticket";
  state: WorkflowState;
  category: ITTicketCategory;
  summary: string;
  urgency: ITTicketUrgency;
  created_at: string;
  updated_at: string;
  action_expires_at: string;
  confirmed_expires_at: string | null;
  confirmation_required: boolean;
  result: ITTicketResult | null;
};

export type ActionListItem = AnnualLeaveActionListItem | ITActionListItem;

export type ActionList = {
  items: ActionListItem[];
  total: number;
};

export type AuditEvent = {
  event_id: string;
  event_type: string;
  revision: number;
  actor_type: string;
  from_state: string | null;
  to_state: string | null;
  safe_metadata: Record<string, unknown>;
  created_at: string;
};

export type AnnualLeaveActionDetail = {
  action_id: string;
  revision: number;
  action_type: "submit_annual_leave";
  state: WorkflowState;
  authoritative_draft: AnnualLeaveDraft;
  created_at: string;
  updated_at: string;
  action_expires_at: string;
  confirmed_at: string | null;
  confirmed_expires_at: string | null;
  confirmation_required: boolean;
  manual_review_required: boolean;
  result: LeaveRequestResult | null;
  audit_events: AuditEvent[];
};

export type ITActionDetail = {
  action_id: string;
  revision: number;
  action_type: "create_it_support_ticket";
  state: WorkflowState;
  authoritative_draft: ITSupportTicketDraft;
  created_at: string;
  updated_at: string;
  action_expires_at: string;
  confirmed_at: string | null;
  confirmed_expires_at: string | null;
  confirmation_required: boolean;
  manual_review_required: boolean;
  result: ITTicketResult | null;
  audit_events: AuditEvent[];
};

export type ActionDetail = AnnualLeaveActionDetail | ITActionDetail;

export type Ticket = {
  ticket_id: string;
  category: ITTicketCategory;
  summary: string;
  description: string;
  urgency: ITTicketUrgency;
  status: ITTicketStatus;
  created_at: string;
  updated_at: string;
};

export type TicketList = { items: Ticket[]; total: number };

export type PolicyDocumentSummary = {
  doc_code: string;
  version: string;
  title: string;
  status: "approved";
  effective_date: string;
  expiry_date: string | null;
  jurisdiction: string;
  audience_groups: string[];
  source_uri: string;
  section_count: number;
};

export type PolicyDocumentList = {
  items: PolicyDocumentSummary[];
  total: number;
};

export type PolicySection = {
  section_label: string;
  anchor: string;
  page: number | null;
  content: string;
};

export type PolicyDocumentDetail = PolicyDocumentSummary & {
  sections: PolicySection[];
};

export type KnowledgeCitation = {
  doc_code: string;
  title: string;
  version: string;
  section_anchor: string;
  page: number | null;
};

export type DurableAction = {
  action_id: string;
  revision: number;
  action_type: "submit_annual_leave" | "create_it_support_ticket";
  state: WorkflowState;
  draft: AuthoritativeDraft;
  action_expires_at: string;
  confirmation_required: boolean;
  authority: "authoritative";
};

export type AssistantResponse = {
  status: "completed" | "unable_to_complete";
  answer: string | null;
  citations: KnowledgeCitation[];
  message: string | null;
  prepared_action: Record<string, unknown> | null;
  action: DurableAction | null;
  action_status: "not_created" | "created" | "reused" | "creation_failed" | null;
  action_not_created_reason: string | null;
};

export type ActionResponse = {
  action_id: string;
  revision: number;
  action_type: "submit_annual_leave" | "create_it_support_ticket";
  state: WorkflowState;
  draft: AuthoritativeDraft;
  action_expires_at: string;
  confirmed_expires_at: string | null;
  confirmation_required: boolean;
  manual_review_required: boolean;
};

export type ConfirmationChallenge = {
  challenge_id: string;
  confirmation_token: string;
  expires_at: string;
  action_id: string;
  revision: number;
  action: ActionResponse;
};

export type ApiErrorPayload = {
  error_code: string;
  message: string;
  request_id: string;
};
