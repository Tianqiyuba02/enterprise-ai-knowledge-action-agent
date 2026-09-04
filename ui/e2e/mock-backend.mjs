import { createServer } from "node:http";

const annualId = "11111111-1111-4111-8111-111111111111";
const itId = "22222222-2222-4222-8222-222222222222";
const succeededId = "33333333-3333-4333-8333-333333333333";
const now = "2026-09-04T01:00:00Z";
let itRevision = 1;
let itSummary = "Laptop will not start";
let itDescription = "The laptop does not respond when the power button is pressed.";

const personas = {
  "demo-v1-7f4c2a91": {
    employee_id: "EMP-1001",
    full_name: "Alex Morgan",
    work_email: "alex.morgan@example.test",
    location: "Melbourne",
    employment_type: "permanent",
    hours_per_day: 7.6,
    work_days: ["monday", "tuesday", "wednesday", "thursday", "friday"],
    timezone: "Australia/Melbourne",
    is_active: true,
  },
  "demo-v1-3b8e6d50": {
    employee_id: "EMP-1002",
    full_name: "Sam Lee",
    work_email: "sam.lee@example.test",
    location: "Melbourne",
    employment_type: "part_time",
    hours_per_day: 6,
    work_days: ["monday", "tuesday", "thursday"],
    timezone: "Australia/Melbourne",
    is_active: true,
  },
};

const leaveDraft = {
  action_type: "submit_annual_leave",
  leave_type: "annual",
  start_date: "2026-09-11",
  end_date: "2026-09-11",
  requested_hours: "7.60",
  projected_balance_hours: "68.40",
  readiness: "ready",
  reason: "Personal day",
  calendar_version: "vic-2026-v1",
  ruleset_version: "annual-leave-v1",
  authority_snapshot_hash: "a".repeat(64),
  scheduled_work_days: 1,
  stable_authority: {
    employee_id: "EMP-1001",
    jurisdiction: "VIC",
    work_days: ["monday", "tuesday", "wednesday", "thursday", "friday"],
    hours_per_day: "7.60",
    timezone: "Australia/Melbourne",
    calendar_version: "vic-2026-v1",
    ruleset_version: "annual-leave-v1",
  },
};

function itDraft() {
  return {
    action_type: "create_it_support_ticket",
    category: "hardware",
    summary: itSummary,
    description: itDescription,
    urgency: "high",
    ruleset_version: "it-support-v1",
    authority_snapshot_hash: "b".repeat(64),
  };
}

function audit(actionId, type, state, revision = 1) {
  return [{
    event_id: `${actionId.slice(0, 8)}-0000-4000-8000-000000000001`,
    event_type: type,
    revision,
    actor_type: "employee",
    from_state: null,
    to_state: state,
    safe_metadata: {},
    created_at: now,
  }];
}

function annualDetail(state = "AWAITING_CONFIRMATION", id = annualId) {
  const result = state === "SUCCEEDED" ? {
    leave_request_id: "44444444-4444-4444-8444-444444444444",
    source_action_id: id,
    leave_type: "annual",
    start_date: leaveDraft.start_date,
    end_date: leaveDraft.end_date,
    requested_hours: leaveDraft.requested_hours,
    reason: leaveDraft.reason,
    status: "submitted",
    submitted_at: now,
    calendar_version: leaveDraft.calendar_version,
    ruleset_version: leaveDraft.ruleset_version,
  } : null;
  return {
    action_id: id,
    revision: 1,
    action_type: "submit_annual_leave",
    state,
    authoritative_draft: leaveDraft,
    created_at: now,
    updated_at: now,
    action_expires_at: "2026-09-05T01:00:00Z",
    confirmed_at: state === "SUCCEEDED" ? now : null,
    confirmed_expires_at: null,
    confirmation_required: state === "AWAITING_CONFIRMATION",
    manual_review_required: false,
    result,
    audit_events: audit(id, state === "SUCCEEDED" ? "action_succeeded" : "action_prepared", state),
  };
}

function itDetail() {
  return {
    action_id: itId,
    revision: itRevision,
    action_type: "create_it_support_ticket",
    state: "AWAITING_CONFIRMATION",
    authoritative_draft: itDraft(),
    created_at: now,
    updated_at: now,
    action_expires_at: "2026-09-05T01:00:00Z",
    confirmed_at: null,
    confirmed_expires_at: null,
    confirmation_required: true,
    manual_review_required: false,
    result: null,
    audit_events: audit(itId, "action_prepared", "AWAITING_CONFIRMATION", itRevision),
  };
}

const policy = {
  doc_code: "POL-HR-001",
  version: "2.0",
  title: "Annual Leave Policy",
  status: "approved",
  effective_date: "2026-01-01",
  expiry_date: null,
  jurisdiction: "VIC",
  audience_groups: ["employees"],
  source_uri: "corpus/v2/annual-leave.md",
  section_count: 1,
};

function json(response, status, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
  response.end(body);
}

async function bodyOf(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const value = Buffer.concat(chunks).toString("utf8");
  return value ? JSON.parse(value) : {};
}

createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1:4010");
  if (url.pathname === "/__health") return json(response, 200, { ok: true });
  const token = request.headers["x-demo-session"];
  const profile = personas[token] ?? personas["demo-v1-7f4c2a91"];
  const path = url.pathname.replace(/^\/api\/v1/, "");

  if (request.method === "GET" && path === "/me/profile") return json(response, 200, profile);
  if (request.method === "GET" && path === "/me/leave/summary") return json(response, 200, {
    balances: [
      { leave_type: "annual", base_balance_hours: "76.00", committed_hours: "0.00", available_hours: profile.employee_id === "EMP-1001" ? "76.00" : "42.00", source_as_of_date: "2026-09-04" },
      { leave_type: "personal", base_balance_hours: "38.00", committed_hours: "0.00", available_hours: "38.00", source_as_of_date: "2026-09-04" },
    ],
    requests: [],
    computed_at: now,
  });
  if (request.method === "GET" && path === "/me/actions") {
    const items = profile.employee_id === "EMP-1001" ? [{
      action_id: annualId,
      revision: 1,
      action_type: "submit_annual_leave",
      state: "AWAITING_CONFIRMATION",
      start_date: leaveDraft.start_date,
      end_date: leaveDraft.end_date,
      requested_hours: leaveDraft.requested_hours,
      reason: leaveDraft.reason,
      created_at: now,
      updated_at: now,
      action_expires_at: "2026-09-05T01:00:00Z",
      confirmed_expires_at: null,
      confirmation_required: true,
      result: null,
    }] : [];
    return json(response, 200, { items, total: items.length });
  }
  if (request.method === "GET" && path === "/me/tickets") return json(response, 200, { items: [], total: 0 });
  if (request.method === "GET" && path === "/knowledge/documents") return json(response, 200, { items: [policy], total: 1 });
  if (request.method === "GET" && path === "/knowledge/documents/POL-HR-001/versions/2.0") return json(response, 200, {
    ...policy,
    sections: [{ section_label: "Carry-over", anchor: "carry-over", page: null, content: "Unused annual leave may carry over subject to the approved policy." }],
  });
  if (request.method === "GET" && path === "/demo/readiness") return json(response, 200, {
    status: "ready", database: true, migration: true, knowledge: true, maintenance: false,
    worker: true, worker_heartbeat_at: now, last_successful_reset_at: now, document_count: 13, chunk_count: 47,
  });
  if (request.method === "GET" && path === "/demo/guided-scenarios") return json(response, 200, { items: [
    { id: "carry-over", label: "Carry over leave", prompt: "Can I carry over unused annual leave?", available: true, note: null },
  ] });
  if (request.method === "GET" && path === `/actions/${annualId}/detail`) return json(response, 200, annualDetail());
  if (request.method === "GET" && path === `/actions/${succeededId}/detail`) return json(response, 200, annualDetail("SUCCEEDED", succeededId));
  if (request.method === "GET" && path === `/actions/${itId}/detail`) return json(response, 200, itDetail());

  if (request.method === "POST" && path === "/assistant/query") return json(response, 200, {
    status: "completed",
    answer: "**Yes.** Approved policy supports carry-over:\n\n- Check your balance\n- Review the governed source\n\n<script>alert('unsafe')</script>",
    citations: [{ doc_code: "POL-HR-001", title: "Annual Leave Policy", version: "2.0", section_anchor: "carry-over", page: null }],
    message: null, prepared_action: null, action: null, action_status: null, action_not_created_reason: null,
  });
  if (request.method === "POST" && path === `/actions/${annualId}/confirmation-challenges`) return json(response, 200, {
    challenge_id: "55555555-5555-4555-8555-555555555555",
    confirmation_token: "e2e-confirmation-token",
    expires_at: "2026-09-04T01:10:00Z",
    action_id: annualId,
    revision: 1,
    action: { ...annualDetail(), draft: leaveDraft },
  });
  if (request.method === "POST" && path === `/actions/${itId}/revisions`) {
    const payload = await bodyOf(request);
    itRevision += 1;
    itSummary = payload.summary;
    itDescription = payload.description;
    return json(response, 200, { ...itDetail(), draft: itDraft() });
  }

  return json(response, 404, { error_code: "not_found", message: "Resource not found.", request_id: "e2e" });
}).listen(4010, "127.0.0.1");
