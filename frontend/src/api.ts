export interface StreamEvent {
  type: "text" | "tool_call" | "tool_result" | "error" | "status";
  data: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  workspaces?: string[];
  workspace_ids?: string[];
  workspace_count?: number;
}

export interface Workspace {
  id: string;
  name: string;
  member_count?: number;
  created_at: string;
}

export interface AuthResponse {
  token: string;
  user: User;
}

const TOKEN_KEY = "agent_platform_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  if (token) {
    return { Authorization: `Bearer ${token}` };
  }
  return {};
}

let _isRedirecting = false;

/** Clear token and redirect to login page. */
export function redirectToLogin(): void {
  if (_isRedirecting) return;
  _isRedirecting = true;
  clearToken();
  window.location.href = "/login";
}

/**
 * Wrapper around fetch that automatically attaches auth headers and
 * intercepts 401 responses to redirect to the login page.
 */
async function apiFetch(url: string, options?: RequestInit): Promise<Response> {
  const resp = await fetch(url, {
    ...options,
    headers: {
      ...options?.headers,
      ...authHeaders(),
    },
  });
  if (resp.status === 401) {
    redirectToLogin();
  }
  return resp;
}

export async function registerUser(email: string, password: string, name: string): Promise<AuthResponse> {
  const resp = await fetch("/api/v1/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, name }),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error?.message || "Registration failed");
  }
  const data = await resp.json();
  setToken(data.token);
  return data;
}

export async function loginUser(email: string, password: string): Promise<AuthResponse> {
  const resp = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error?.message || "Login failed");
  }
  const data = await resp.json();
  setToken(data.token);
  return data;
}

export async function getCurrentUser(): Promise<User> {
  const resp = await apiFetch("/api/v1/users/me");
  if (!resp.ok) throw new Error("Not authenticated");
  return resp.json();
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const resp = await apiFetch("/api/v1/workspaces");
  if (!resp.ok) throw new Error("Failed to list workspaces");
  return resp.json();
}

export async function createWorkspace(name: string): Promise<Workspace> {
  const resp = await apiFetch("/api/v1/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!resp.ok) throw new Error("Failed to create workspace");
  return resp.json();
}

export interface ObservabilitySummary {
  total_requests: number;
  avg_latency_ms: number;
  total_tokens: number;
  error_rate: number;
  active_sessions: number;
}

export interface RequestLog {
  id: string;
  trace_id: string;
  model: string;
  status_code: number;
  duration_ms: number;
  error: string;
  created_at: string;
}

export interface DailyToken {
  date: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface LatencyData {
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  over_time: { bucket: string; p50: number; p95: number; p99: number }[];
}

export interface ErrorGroup {
  error_type: string;
  count: number;
  last_seen: string;
}

export interface QuotaInfo {
  max_tokens_per_day: number;
  max_cost_per_month: number;
  usage_today: number;
  tokens_used: number;
  cost_today: number;
}

export interface OTelConfig {
  enabled: boolean;
  endpoint: string;
  headers: Record<string, string>;
}

export async function getObservabilitySummary(wsId: string): Promise<ObservabilitySummary> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/observability/summary`);
  if (!resp.ok) throw new Error("Failed to fetch summary");
  return resp.json();
}

export async function getObservabilityRequests(wsId: string, params?: { limit?: number; offset?: number; status?: number; model?: string; since?: string }): Promise<RequestLog[]> {
  const qs = new URLSearchParams();
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  if (params?.status) qs.set("status", String(params.status));
  if (params?.model) qs.set("model", params.model);
  if (params?.since) qs.set("since", params.since);
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/observability/requests?${qs}`);
  if (!resp.ok) throw new Error("Failed to fetch requests");
  return resp.json();
}

export async function getTokenDaily(wsId: string, since?: string, until?: string): Promise<DailyToken[]> {
  const qs = new URLSearchParams();
  if (since) qs.set("since", since);
  if (until) qs.set("until", until);
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/observability/tokens/daily?${qs}`);
  if (!resp.ok) throw new Error("Failed to fetch token data");
  return resp.json();
}

export async function getRequestDetail(wsId: string, traceId: string): Promise<Record<string, unknown>> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/observability/requests/${traceId}`);
  if (!resp.ok) throw new Error("Failed to fetch request detail");
  return resp.json();
}

export async function getLatency(wsId: string, since?: string, until?: string): Promise<LatencyData> {
  const qs = new URLSearchParams();
  if (since) qs.set("since", since);
  if (until) qs.set("until", until);
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/observability/latency?${qs}`);
  if (!resp.ok) throw new Error("Failed to fetch latency data");
  return resp.json();
}

export async function getErrors(wsId: string, since?: string): Promise<ErrorGroup[]> {
  const qs = new URLSearchParams();
  if (since) qs.set("since", since);
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/observability/errors?${qs}`);
  if (!resp.ok) throw new Error("Failed to fetch errors");
  return resp.json();
}

export async function getQuota(wsId: string): Promise<QuotaInfo> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/quota`);
  if (!resp.ok) throw new Error("Failed to fetch quota");
  return resp.json();
}

export async function updateQuota(wsId: string, data: { max_tokens_per_day?: number; max_cost_per_month?: number }): Promise<{ quota: QuotaInfo }> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/quota`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) throw new Error("Failed to update quota");
  return resp.json();
}

export async function getOtelSettings(wsId: string): Promise<OTelConfig> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/settings/otel`);
  if (!resp.ok) throw new Error("Failed to fetch OTel settings");
  return resp.json();
}

export async function updateOtelSettings(wsId: string, config: OTelConfig): Promise<{ otel: OTelConfig }> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/settings/otel`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!resp.ok) throw new Error("Failed to update OTel settings");
  return resp.json();
}

export async function listAdminUsers(): Promise<User[]> {
  const resp = await apiFetch("/api/v1/admin/users");
  if (!resp.ok) throw new Error("Failed to list users");
  return resp.json();
}

// ─── Admin: Tenants ───────────────────────────────────────────────────────

export interface Tenant {
  id: string;
  name: string;
  domain: string;
  user_count: number;
  workspace_count: number;
  created_at: string;
}

export async function fetchTenants(): Promise<Tenant[]> {
  const resp = await apiFetch("/api/v1/admin/tenants");
  if (!resp.ok) throw new Error("Failed to fetch tenants");
  return resp.json();
}

export async function updateTenant(id: string, data: Partial<Tenant>): Promise<Tenant> {
  const resp = await apiFetch(`/api/v1/admin/tenants/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) throw new Error("Failed to update tenant");
  return resp.json();
}

// ─── Admin: Users ─────────────────────────────────────────────────────────

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  role: string;
  workspaces: string[];
  last_login: string | null;
  created_at: string;
}

export async function fetchUsers(params?: { search?: string; role?: string; workspace_id?: string }): Promise<AdminUser[]> {
  const q = new URLSearchParams();
  if (params?.search) q.set("search", params.search);
  if (params?.role) q.set("role", params.role);
  if (params?.workspace_id) q.set("workspace_id", params.workspace_id);
  const resp = await apiFetch(`/api/v1/admin/users?${q}`);
  if (!resp.ok) throw new Error("Failed to fetch users");
  return resp.json();
}

export async function updateUser(id: string, data: { role?: string; workspace_ids?: string[] }): Promise<AdminUser> {
  const resp = await apiFetch(`/api/v1/admin/users/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) throw new Error("Failed to update user");
  return resp.json();
}

export async function deleteUser(id: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/admin/users/${id}`, { method: "DELETE" });
  if (!resp.ok) throw new Error("Failed to delete user");
}

export async function inviteUser(data: { email: string; role: string; workspace_id?: string }): Promise<AdminUser> {
  const resp = await apiFetch("/api/v1/admin/users/invite", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) throw new Error("Failed to invite user");
  return resp.json();
}

// ─── Admin: Workspaces ────────────────────────────────────────────────────

export interface AdminWorkspace {
  id: string;
  name: string;
  member_count: number;
  agent_count: number;
  owner: string;
  created_at: string;
}

export async function fetchAdminWorkspaces(): Promise<AdminWorkspace[]> {
  const resp = await apiFetch("/api/v1/admin/workspaces");
  if (!resp.ok) throw new Error("Failed to fetch workspaces");
  return resp.json();
}

export async function updateAdminWorkspace(id: string, data: { name?: string; settings?: Record<string, unknown>; max_tokens_per_day?: number; max_cost_per_month?: number }): Promise<AdminWorkspace> {
  const resp = await apiFetch(`/api/v1/admin/workspaces/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) throw new Error("Failed to update workspace");
  return resp.json();
}

export async function archiveWorkspace(id: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/admin/workspaces/${id}`, { method: "DELETE" });
  if (!resp.ok) throw new Error("Failed to archive workspace");
}

// ─── Admin: Audit ─────────────────────────────────────────────────────────

export interface AuditEntry {
  id: string;
  action: string;
  user_id: string;
  target_type: string;
  target_id: string;
  details: Record<string, unknown>;
  ip_address: string;
  created_at: string;
}

export interface AuditResponse {
  items: AuditEntry[];
  total: number;
}

export async function fetchAdminAudit(params?: { action?: string; user_id?: string; since?: string; until?: string; limit?: number; offset?: number }): Promise<AuditResponse> {
  const q = new URLSearchParams();
  if (params?.action) q.set("action", params.action);
  if (params?.user_id) q.set("user_id", params.user_id);
  if (params?.since) q.set("since", params.since);
  if (params?.until) q.set("until", params.until);
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.offset) q.set("offset", String(params.offset));
  const resp = await apiFetch(`/api/v1/admin/audit?${q}`);
  if (!resp.ok) throw new Error("Failed to fetch audit log");
  return resp.json();
}

export async function fetchWorkspaceAudit(workspaceId: string, params?: { action?: string; since?: string; until?: string; limit?: number; offset?: number }): Promise<AuditResponse> {
  const q = new URLSearchParams();
  if (params?.action) q.set("action", params.action);
  if (params?.since) q.set("since", params.since);
  if (params?.until) q.set("until", params.until);
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.offset) q.set("offset", String(params.offset));
  const resp = await apiFetch(`/api/v1/workspaces/${workspaceId}/audit?${q}`);
  if (!resp.ok) throw new Error("Failed to fetch workspace audit");
  return resp.json();
}

// ─── Admin: Usage ─────────────────────────────────────────────────────────

export interface UsageData {
  total_requests: number;
  total_tokens: number;
  total_cost: number;
  by_workspace: { workspace_id: string; total_requests: number; total_tokens: number; total_cost: number }[];
}

export async function fetchUsage(params?: { tenant_id?: string; since?: string; until?: string }): Promise<UsageData> {
  const q = new URLSearchParams();
  if (params?.tenant_id) q.set("tenant_id", params.tenant_id);
  if (params?.since) q.set("since", params.since);
  if (params?.until) q.set("until", params.until);
  const resp = await apiFetch(`/api/v1/admin/usage?${q}`);
  if (!resp.ok) throw new Error("Failed to fetch usage");
  return resp.json();
}

export async function* streamChat(
  messages: { role: string; content: string }[],
  config?: Record<string, unknown>,
): AsyncGenerator<StreamEvent> {
  const response = await apiFetch("/api/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, config }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.error?.message || `Chat failed: ${response.status}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const payload = line.slice(6).trim();
        if (payload === "[DONE]") return;
        yield JSON.parse(payload) as StreamEvent;
      }
    }
  }
}
