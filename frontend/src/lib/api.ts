const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1").replace(/\/$/, "");

export function getCaseWebSocketUrl(caseId: string, token: string): string {
  const apiUrl = new URL(API_BASE, window.location.origin);
  apiUrl.protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  apiUrl.pathname = `${apiUrl.pathname.replace(/\/$/, "")}/cases/${encodeURIComponent(caseId)}/ws`;
  apiUrl.search = new URLSearchParams({ token }).toString();
  return apiUrl.toString();
}

export interface CaseListItem {
  case_id: string;
  workflow_state: string;
  current_decision: string | null;
  current_sub_reason: string | null;
  decision_confidence: number | null;
  document_count: number;
  is_complete: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface CaseListResponse {
  total_count: number;
  page: number;
  page_size: number;
  items: CaseListItem[];
}

export interface CaseDocument {
  document_id: string;
  file_name: string;
  checksum: string;
  size_bytes: number;
  mime_type: string;
  doc_type: string;
  ocr_payload_uri: string | null;
}

export interface HumanReviewItem {
  id: string;
  reviewer_id: string;
  action: string;
  new_decision?: string | null;
  reason_code: string;
  remarks: string;
  expected_case_version: number;
  created_at: string;
}

export interface CaseDetailResponse extends CaseListItem {
  workflow_version: string;
  missing_documents: string[];
  rule_id_matched: string | null;
  justification: string | null;
  detailed_explanation: string | null;
  agent_findings: Record<string, any> | null;
  documents: CaseDocument[];
  human_reviews?: HumanReviewItem[];
  latest_human_review?: HumanReviewItem | null;
}

export interface ReviewLockResponse {
  case_id: string;
  reviewer_id: string;
  locked_at: string;
  expires_at: string;
  lock_active: boolean;
}

export interface AuditVerifyResponse {
  case_id: string;
  chain_intact: boolean;
  total_events: number;
  genesis_hash: string;
  terminal_hash: string;
  events: Array<Record<string, unknown>>;
}

export interface OperationalMetricsResponse {
  timestamp: string;
  active_cases: number;
  cases_awaiting_human: number;
  cases_awaiting_info: number;
  completed_cases_24h: number;
  straight_through_processing_rate: number | null;
  average_adjudication_latency_ms: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  intake_queue_depth: number;
  workflow_queue_depth: number;
  auto_approval_rate: number | null;
  escalation_rate: number | null;
  rejection_rate: number | null;
  total_adjudicated_cases: number;
  insufficient_data: boolean;
  llm_runtime: {
    scope: string;
    calls_total: number;
    calls_success: number;
    calls_failed: number;
    provider_attempts: number;
    provider_attempts_success: number;
    provider_attempts_failed: number;
    provider_failures: Record<string, number>;
    provider_failure_categories: Record<string, number>;
    stages: Record<string, {
      calls_total: number;
      calls_success: number;
      calls_failed: number;
      latency_p50_ms: number | null;
      latency_p95_ms: number | null;
    }>;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    responses_with_token_usage: number;
    responses_without_token_usage: number;
    token_usage_complete: boolean;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
    provider_latency_p50_ms: number | null;
    provider_latency_p95_ms: number | null;
  };
  period_start: string | null;
  period_end: string | null;
}

export interface BenchmarkEvaluationResponse {
  total_documents: number;
  total_clusters: number;
  classification_accuracy_percentage: number;
  member_resolution_percentage: number;
  fraud_label_summary: {
    source: string;
    total_documents: number;
    labelled_documents: number;
    positive_documents: number;
    negative_documents: number;
    unlabelled_documents: number;
    positive_by_reason: Array<{
      reason: string;
      count: number;
    }>;
  } | null;
  categories: Array<{
    category: string;
    total: number;
    correct: number;
    accuracy_percentage: number;
  }>;
  ocr_samples: Array<{
    document_name: string;
    category: string;
    text_spans: number;
    kv_pairs_count: number;
    quality_score: number;
    latency_ms: number;
    sample_keys: string[];
  }>;
  fraud_tests: Array<{
    test_name: string;
    description: string;
    status: string;
    fraud_review_required: boolean;
    score: number;
    details: string;
  }>;
  last_run_timestamp: string;
  execution_time_seconds: number;
  suite_status: string;
}

export interface CalibrationBin {
  bin_index: number;
  lower_bound: number;
  upper_bound: number;
  mean_predicted_confidence: number;
  empirical_accuracy: number;
  sample_count: number;
  weight: number;
}

export interface SelectiveCoverageItem {
  confidence_threshold: number;
  coverage: number;
  selective_accuracy: number;
  accepted_samples: number;
}

export interface CalibrationMetricsResponse {
  strategy: string;
  total_samples: number;
  evaluation_source: string;
  evaluation_label: string;
  evaluation_population: string;
  brier_score: number;
  expected_calibration_error: number;
  maximum_calibration_error: number;
  raw_confidence_mean: number;
  calibrated_confidence_mean: number | null;
  calibrated_brier_score: number | null;
  calibrated_expected_calibration_error: number | null;
  calibration_method: string;
  bins: CalibrationBin[];
  calibrated_bins: CalibrationBin[];
  selective_risk_coverage: SelectiveCoverageItem[];
  interpretation: string;
}

export async function fetchCalibrationMetrics(
  strategy = "equal_width",
  bins = 10
): Promise<CalibrationMetricsResponse> {
  const res = await apiFetch(`/metrics/calibration?strategy=${strategy}&bins_count=${bins}`);
  if (!res.ok) throw new Error("Failed to fetch confidence calibration metrics");
  return res.json();
}

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (typeof window !== "undefined") {
    const token =
      window.localStorage.getItem("medishield_access_token") ||
      window.sessionStorage.getItem("medishield_access_token");
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  headers.set("X-Correlation-ID", crypto.randomUUID());
  if (init.method && init.method !== "GET") {
    headers.set("Idempotency-Key", crypto.randomUUID());
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string; message?: string };
      message = body.detail ?? body.message ?? message;
    } catch {
      // Keep the HTTP status message when the response has no JSON body.
    }
    throw new Error(message);
  }
  return response;
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  return (await response.json()) as T;
}

export function getCases(options: {
  state?: string;
  page?: number;
  page_size?: number;
} = {}): Promise<CaseListResponse> {
  const query = new URLSearchParams();
  if (options.state) query.set("workflow_state", options.state);
  if (options.page !== undefined) query.set("page", String(options.page));
  if (options.page_size !== undefined) query.set("page_size", String(options.page_size));
  const suffix = query.size ? `?${query.toString()}` : "";
  return getJson(`/cases${suffix}`);
}

export function submitIntake(formData: FormData): Promise<unknown> {
  return getJson("/cases", { method: "POST", body: formData });
}

export function getCaseDetail(caseId: string): Promise<CaseDetailResponse> {
  return getJson(`/cases/${encodeURIComponent(caseId)}`);
}

export function acquireReviewLock(caseId: string): Promise<ReviewLockResponse> {
  return getJson(`/cases/${encodeURIComponent(caseId)}/review/lock`, { method: "POST" });
}

export async function releaseReviewLock(caseId: string): Promise<void> {
  await apiFetch(`/cases/${encodeURIComponent(caseId)}/review/lock`, { method: "DELETE" });
}

export function submitConfirmation(input: {
  case_id: string;
  expected_case_version: number;
  new_decision: string;
  reason_code: string;
  remarks: string;
}): Promise<unknown> {
  return getJson(`/cases/${encodeURIComponent(input.case_id)}/review/confirm`, {
    method: "POST",
    body: JSON.stringify({
      new_decision: input.new_decision,
      reason_code: input.reason_code,
      remarks: input.remarks,
      expected_case_version: input.expected_case_version,
    }),
  });
}

export function submitOverride(input: {
  case_id: string;
  expected_case_version: number;
  new_decision: string;
  reason_code: string;
  remarks: string;
}): Promise<unknown> {
  return getJson(`/cases/${encodeURIComponent(input.case_id)}/review/override`, {
    method: "POST",
    body: JSON.stringify({
      new_decision: input.new_decision,
      reason_code: input.reason_code,
      remarks: input.remarks,
      expected_case_version: input.expected_case_version,
    }),
  });
}

export function requestInformation(input: {
  case_id: string;
  expected_case_version: number;
  missing_doc_types: string[];
  remarks: string;
}): Promise<unknown> {
  return getJson(`/cases/${encodeURIComponent(input.case_id)}/review/request-info`, {
    method: "POST",
    body: JSON.stringify({
      expected_case_version: input.expected_case_version,
      requested_document_types: input.missing_doc_types,
      remarks: input.remarks,
    }),
  });
}

export interface CaseExplanationResponse {
  case_id: string;
  decision: string;
  rule_id_matched: string;
  decision_confidence: number | null;
  detailed_explanation: string;
  model_source: string;
  generated_at: string;
}

export function explainCase(caseId: string): Promise<CaseExplanationResponse> {
  return getJson(`/cases/${encodeURIComponent(caseId)}/explain`, { method: "POST" });
}

export function rerunCase(caseId: string): Promise<unknown> {
  return getJson(`/cases/${encodeURIComponent(caseId)}/rerun`, { method: "POST" });
}

export interface AgentExecutionEvent {
  event_id: string;
  case_id: string;
  run_id: string;
  agent_name: string;
  event_type: "STARTED" | "HEARTBEAT" | "COMPLETED" | "FAILED" | "SKIPPED" | "RETRYING";
  status: "RUNNING" | "SUCCESS" | "ERROR" | "BLOCKED";
  sequence_number: number;
  timestamp: string;
  duration_ms: number | null;
  safe_summary: string;
  payload: Record<string, any>;
}

export function fetchCaseEvents(caseId: string): Promise<AgentExecutionEvent[]> {
  return getJson(`/cases/${encodeURIComponent(caseId)}/events`);
}

export function fetchDocumentOcr(caseId: string, documentId: string): Promise<unknown> {
  return getJson(
    `/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(documentId)}/ocr`
  );
}

export async function fetchDocumentImage(caseId: string, documentId: string): Promise<string> {
  const response = await apiFetch(
    `/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(documentId)}/content`
  );
  return URL.createObjectURL(await response.blob());
}

export async function downloadCaseReport(caseId: string): Promise<Blob> {
  const response = await apiFetch(`/cases/${encodeURIComponent(caseId)}/report`);
  return response.blob();
}

export function getAuditVerification(caseId: string): Promise<AuditVerifyResponse> {
  return getJson(`/audit/cases/${encodeURIComponent(caseId)}`);
}

export function getOperationalMetrics(): Promise<OperationalMetricsResponse> {
  return getJson("/metrics/operational");
}

export function getBenchmarkMetrics(): Promise<BenchmarkEvaluationResponse> {
  return getJson("/metrics/benchmark");
}

export function runBenchmarkEvaluation(): Promise<BenchmarkEvaluationResponse> {
  return getJson("/metrics/benchmark/run", { method: "POST" });
}

// ── 3-Tier User Persona API Functions (ADR-012) ──────────────────────────────

export interface ClaimerDocumentItem {
  document_id: string;
  file_name: string;
  doc_type: string;
  size_bytes: number;
  created_at: string;
}

export interface ClaimerAdjudicationSummary {
  decision: string;
  covered_amount: number | null;
  total_billed: number | null;
  deductible_applied: number | null;
  copay_percentage: number | null;
  member_responsibility: number | null;
  explanation_letter: string | null;
}

export interface ClaimerStatusResponse {
  case_id: string;
  workflow_state: string;
  is_complete: boolean;
  status_label: string;
  missing_documents: string[];
  adjudication: ClaimerAdjudicationSummary | null;
  submitted_documents: ClaimerDocumentItem[];
  created_at: string;
  updated_at: string;
}

export function getClaimerStatus(caseId: string): Promise<ClaimerStatusResponse> {
  return getJson(`/cases/${encodeURIComponent(caseId)}/status`, {
    headers: { "X-User-Role": "CLAIMER" },
  });
}

export interface MedicalCodeItem {
  id?: string;
  code: string;
  coding_system: string;
  short_description: string;
  long_description?: string;
  category?: string;
  standard_charge?: number;
  requires_prior_auth: boolean;
  effective_from?: string;
  effective_to?: string;
  is_active: boolean;
}

export interface CodeUploadSummary {
  records_processed: number;
  records_inserted: number;
  coding_systems_updated: string[];
  errors_count: number;
  error_sample: string[];
  message: string;
}

export interface CodeListResponse {
  total: number;
  limit: number;
  offset: number;
  items: MedicalCodeItem[];
}

export interface AdminDashboardMetrics {
  total_cases: number;
  workflow_distribution: Record<string, number>;
  decision_distribution: Record<string, number>;
  straight_through_processing_rate: number;
  escalation_reason_distribution: Record<string, number>;
  total_medical_codes: number;
  active_codes_by_system: Record<string, number>;
  active_policies_count: number;
  system_status: string;
}

export function getAdminDashboardMetrics(): Promise<AdminDashboardMetrics> {
  return getJson("/admin/dashboard", {
    headers: { "X-User-Role": "ADMINISTRATOR" },
  });
}

export function listMedicalCodes(query = "", codingSystem = "", limit = 50, offset = 0): Promise<CodeListResponse> {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  if (codingSystem) params.set("coding_system", codingSystem);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return getJson(`/admin/codes?${params.toString()}`, {
    headers: { "X-User-Role": "ADMINISTRATOR" },
  });
}

export async function uploadMedicalCodesCsv(file: File): Promise<CodeUploadSummary> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await apiFetch("/admin/codes/upload", {
    method: "POST",
    headers: { "X-User-Role": "ADMINISTRATOR" },
    body: formData,
  });
  return response.json();
}

export interface PolicyVersionItem {
  policy_id: string;
  plan_name: string;
  policy_type: string;
  is_active: boolean;
  version_tag: string;
  effective_start_date?: string;
  effective_end_date?: string;
  is_published: boolean;
  published_by?: string;
  published_at?: string;
  source_pdf_key: string;
  chunk_count: number;
}

export function listPolicies(): Promise<PolicyVersionItem[]> {
  return getJson("/policies", {
    headers: { "X-User-Role": "ADMINISTRATOR" },
  });
}

export async function uploadPolicyPdf(formData: FormData): Promise<any> {
  const response = await apiFetch("/policies", {
    method: "POST",
    headers: { "X-User-Role": "ADMINISTRATOR" },
    body: formData,
  });
  return response.json();
}

export async function publishPolicyVersion(
  policyId: string,
  versionTag: string,
  justification: string = "Corporate policy publishing through ADR-006 firewall"
): Promise<any> {
  const response = await apiFetch(`/policies/${policyId}/publish`, {
    method: "POST",
    headers: { "X-User-Role": "ADMINISTRATOR" },
    body: JSON.stringify({
      version_tag: versionTag,
      change_justification: justification,
    }),
  });
  return response.json();
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: string;
  username: string;
  user_id: string;
}

export async function loginApi(username: string, password: string): Promise<LoginResponse> {
  const response = await apiFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  return response.json();
}