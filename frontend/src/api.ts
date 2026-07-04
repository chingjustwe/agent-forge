export interface StreamEvent {
  type: "text" | "tool_call" | "tool_result" | "error" | "status";
  data: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;  // P0-2: 仅 tenant_admin / member（tenant 级）。workspace 级角色查 WorkspaceMember
  workspaces?: string[];
  workspace_ids?: string[];
  workspace_count?: number;
}

export interface Workspace {
  id: string;
  name: string;
  slug?: string;
  description?: string;
  icon?: string;
  owner_id?: string;
  member_count?: number;
  created_at: string;
  updated_at?: string;
}

export interface WorkspaceMembership {
  id: string;
  name: string;
  role: string;  // workspace 级角色: member/viewer/workspace_admin/workspace_owner
  created_at?: string;
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
  // 通知 WorkspaceProvider 等 token 消费方重新拉取数据
  window.dispatchEvent(new CustomEvent("auth:token-changed"));
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  window.dispatchEvent(new CustomEvent("auth:token-changed"));
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

export async function getInvite(token: string): Promise<InviteInfo> {
  const resp = await fetch(`/api/v1/auth/invite?token=${encodeURIComponent(token)}`);
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error?.message || "Invalid invite link");
  }
  return resp.json();
}

export async function acceptInvite(data: AcceptInviteRequest): Promise<AuthResponse> {
  const resp = await fetch("/api/v1/auth/accept-invite", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error?.message || "Failed to accept invite");
  }
  const result = await resp.json();
  setToken(result.token);
  return result;
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

export async function listMyWorkspaces(): Promise<WorkspaceMembership[]> {
  const resp = await apiFetch("/api/v1/me/workspaces");
  if (!resp.ok) throw new Error("Failed to fetch my workspaces");
  return resp.json();
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const resp = await apiFetch("/api/v1/workspaces");
  if (!resp.ok) throw new Error("Failed to list workspaces");
  return resp.json();
}

export async function createWorkspace(
  name: string,
  options?: { slug?: string; description?: string; icon?: string },
): Promise<Workspace> {
  const resp = await apiFetch("/api/v1/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, ...(options || {}) }),
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

export interface InviteInfo {
  email: string;
  role: string;
}

export interface AcceptInviteRequest {
  token: string;
  password: string;
  name: string;
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  role: string;  // P0-2: 仅 tenant_admin / member（tenant 级）。workspace 级角色查 WorkspaceMember
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
  // P0-2: role 仅接受 tenant_admin / member（tenant 级）。workspace 级角色通过 WorkspaceMember 接口管理。
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
  slug?: string;
  description?: string;
  icon?: string;
  owner_id?: string;
  member_count: number;
  agent_count: number;
  owner: string;
  is_default: boolean;
  // P3-3: present when the backend returns archived workspaces alongside
  // active ones (via `include_archived=true`). Forward-compatible — older
  // backends omit the field, in which case the workspace is treated as
  // active.
  archived?: boolean;
  created_at: string;
  updated_at?: string;
}

export async function fetchAdminWorkspaces(
  options?: { include_archived?: boolean },
): Promise<AdminWorkspace[]> {
  const q = new URLSearchParams();
  if (options?.include_archived) q.set("include_archived", "true");
  const qs = q.toString();
  const url = qs ? `/api/v1/admin/workspaces?${qs}` : "/api/v1/admin/workspaces";
  const resp = await apiFetch(url);
  if (!resp.ok) throw new Error("Failed to fetch workspaces");
  return resp.json();
}

export async function createAdminWorkspace(
  name: string,
  options?: { slug?: string; description?: string; icon?: string },
): Promise<AdminWorkspace> {
  const resp = await apiFetch("/api/v1/admin/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, ...(options || {}) }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to create workspace");
  }
  return resp.json();
}

export async function updateAdminWorkspace(
  id: string,
  data: {
    name?: string;
    slug?: string;
    description?: string;
    icon?: string;
    settings?: Record<string, unknown>;
    max_tokens_per_day?: number;
    max_cost_per_month?: number;
  },
): Promise<AdminWorkspace> {
  const resp = await apiFetch(`/api/v1/admin/workspaces/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to update workspace");
  }
  return resp.json();
}

export async function archiveWorkspace(id: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/admin/workspaces/${id}`, { method: "DELETE" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to archive workspace");
  }
}

// P3-3: hard-delete an archived workspace and all its data. Requires the
// exact workspace name as `confirmName` (two-step confirmation).
export async function purgeWorkspace(
  workspaceId: string,
  confirmName: string,
): Promise<{ purged: boolean; workspace_id: string }> {
  const resp = await apiFetch(`/api/v1/admin/workspaces/${workspaceId}/purge`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ purge_confirm: confirmName }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to purge workspace");
  }
  return resp.json();
}

// P3-4: atomically transfer the is_default flag to the given workspace.
export async function setDefaultWorkspace(
  workspaceId: string,
): Promise<{ id: string; is_default: boolean }> {
  const resp = await apiFetch(`/api/v1/admin/workspaces/${workspaceId}/set-default`, {
    method: "POST",
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to set default workspace");
  }
  return resp.json();
}

// ─── Workspace Members ─────────────────────────────────────────────────────

export interface WorkspaceMember {
  user_id: string;
  email: string;
  name: string;
  role: string;
}

export async function fetchWorkspaceMembers(workspaceId: string): Promise<WorkspaceMember[]> {
  const resp = await apiFetch(`/api/v1/workspaces/${workspaceId}/members`);
  if (!resp.ok) throw new Error("Failed to fetch members");
  return resp.json();
}

export async function addWorkspaceMember(workspaceId: string, userId: string, role: string = "member"): Promise<void> {
  const resp = await apiFetch(`/api/v1/workspaces/${workspaceId}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, role }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to add member");
  }
}

export async function removeWorkspaceMember(workspaceId: string, userId: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/workspaces/${workspaceId}/members/${userId}`, {
    method: "DELETE",
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to remove member");
  }
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
  sessionId?: string,
): AsyncGenerator<StreamEvent> {
  const fullConfig: Record<string, unknown> = { ...(config || {}) };
  if (sessionId) fullConfig.session_id = sessionId;

  const response = await apiFetch("/api/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, config: fullConfig }),
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

// ─── Chat Sessions (P1-1) ─────────────────────────────────────────────────

export interface ChatSessionInfo {
  id: string;
  workspace_id: string;
  owner_id: string;
  title: string;
  visibility: "private" | "workspace";
  agent_name: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageInfo {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  tokens: number;
  created_at: string;
}

export async function listSessions(wsId: string): Promise<ChatSessionInfo[]> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/sessions`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to list sessions");
  }
  return resp.json();
}

export async function createSession(
  wsId: string,
  data: { title?: string; visibility?: string; agent_name?: string },
): Promise<ChatSessionInfo> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to create session");
  }
  return resp.json();
}

export async function getSession(
  wsId: string,
  sessionId: string,
): Promise<{ session: ChatSessionInfo; messages: ChatMessageInfo[] }> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/sessions/${sessionId}`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to fetch session");
  }
  return resp.json();
}

export async function updateSession(
  wsId: string,
  sessionId: string,
  data: { title?: string; visibility?: string },
): Promise<ChatSessionInfo> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to update session");
  }
  return resp.json();
}

export async function deleteSession(wsId: string, sessionId: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!resp.ok && resp.status !== 204) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to delete session");
  }
}

// ─── Session Shares (P3-5) ─────────────────────────────────────────────────
// Backend response shape: only ids/timestamps are returned. The frontend
// cross-references fetchWorkspaceMembers() to resolve user_email/user_name.

export interface SessionShare {
  session_id: string;
  user_id: string;
  shared_by: string;
  shared_at: string;
}

export async function listSessionShares(sessionId: string): Promise<SessionShare[]> {
  const resp = await apiFetch(`/api/v1/sessions/${sessionId}/shares`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to list shares");
  }
  return resp.json();
}

export async function createSessionShare(
  sessionId: string,
  userId: string,
): Promise<SessionShare> {
  const resp = await apiFetch(`/api/v1/sessions/${sessionId}/shares`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to share session");
  }
  return resp.json();
}

export async function deleteSessionShare(
  sessionId: string,
  userId: string,
): Promise<void> {
  const resp = await apiFetch(`/api/v1/sessions/${sessionId}/shares/${userId}`, {
    method: "DELETE",
  });
  if (!resp.ok && resp.status !== 204) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to revoke share");
  }
}

// ─── Workspace Invitations (P2-1) ──────────────────────────────────────────

export interface WorkspaceInvitation {
  id: string;
  workspace_id: string;
  workspace_name?: string | null;
  email: string | null;
  role: string;
  token: string;
  invited_by: string;
  expires_at: string;
  accepted_at: string | null;
  accepted_by: string | null;
  created_at: string;
  is_expired: boolean;
  is_accepted: boolean;
}

export interface WorkspaceInvitationPreview {
  id: string;
  workspace_id: string;
  workspace_name: string | null;
  email: string | null;
  role: string;
  expires_at: string;
  accepted_at: string | null;
  accepted_by: string | null;
  created_at: string;
  is_expired: boolean;
  is_accepted: boolean;
}

export async function listWorkspaceInvitations(wsId: string): Promise<WorkspaceInvitation[]> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/invitations`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to list invitations");
  }
  return resp.json();
}

export async function createWorkspaceInvitation(
  wsId: string,
  data: { email?: string | null; role?: string; expires_in_days?: number },
): Promise<WorkspaceInvitation> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/invitations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to create invitation");
  }
  return resp.json();
}

export async function revokeWorkspaceInvitation(wsId: string, invitationId: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/invitations/${invitationId}`, {
    method: "DELETE",
  });
  if (!resp.ok && resp.status !== 204) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to revoke invitation");
  }
}

/** Public preview — uses plain fetch (no auth header) so logged-out users can
 * see what they were invited to before signing in. */
export async function getInvitationPreview(token: string): Promise<WorkspaceInvitationPreview> {
  const resp = await fetch(`/api/v1/invitations/${encodeURIComponent(token)}`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Invitation not found");
  }
  return resp.json();
}

export async function acceptWorkspaceInvitation(token: string): Promise<{ workspace_id: string; role: string; already_member: boolean }> {
  const resp = await apiFetch(`/api/v1/invitations/${encodeURIComponent(token)}/accept`, {
    method: "POST",
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to accept invitation");
  }
  return resp.json();
}

// ─── Agents (P2-2) ─────────────────────────────────────────────────────────

export type AgentFramework = "direct_llm" | "adk" | "langgraph";

export interface AgentConfig {
  id: string;
  workspace_id: string;
  name: string;
  framework: AgentFramework;
  config: Record<string, unknown>;
  created_by: string;
  created_at: string;
  updated_at?: string;
}

export async function listAgents(wsId: string): Promise<AgentConfig[]> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/agents`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to list agents");
  }
  return resp.json();
}

export async function getAgent(wsId: string, agentId: string): Promise<AgentConfig> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/agents/${agentId}`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to fetch agent");
  }
  return resp.json();
}

export async function createAgent(
  wsId: string,
  data: { name: string; framework: AgentFramework; config?: Record<string, unknown> },
): Promise<AgentConfig> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: data.name, framework: data.framework, config: data.config || {} }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to create agent");
  }
  return resp.json();
}

export async function updateAgent(
  wsId: string,
  agentId: string,
  data: { name?: string; framework?: AgentFramework; config?: Record<string, unknown> },
): Promise<AgentConfig> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/agents/${agentId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to update agent");
  }
  return resp.json();
}

export async function deleteAgent(wsId: string, agentId: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/agents/${agentId}`, {
    method: "DELETE",
  });
  if (!resp.ok && resp.status !== 204) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to delete agent");
  }
}

// P3-2: cross-workspace copy (tenant_admin only). Deep-copies an agent
// config into a target workspace within the same tenant.
export async function copyAgentToWorkspace(
  workspaceId: string,
  agentId: string,
  targetWorkspaceId: string,
): Promise<AgentConfig> {
  const resp = await apiFetch(
    `/api/v1/workspaces/${workspaceId}/agents/${agentId}/copy-to/${targetWorkspaceId}`,
    { method: "POST" },
  );
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to copy agent");
  }
  return resp.json();
}

// ─── API Keys (P2-3) ───────────────────────────────────────────────────────

export type ApiKeyScope = "chat:write" | "quota:read" | "sessions:read" | "sessions:write";

export const API_KEY_SCOPES: { value: ApiKeyScope; label: string }[] = [
  { value: "chat:write", label: "Chat (write)" },
  { value: "quota:read", label: "Quota (read)" },
  { value: "sessions:read", label: "Sessions (read)" },
  { value: "sessions:write", label: "Sessions (write)" },
];

/** List response — never contains the plaintext key. */
export interface ApiKeyInfo {
  id: string;
  name: string;
  key_prefix: string;
  scopes: ApiKeyScope[];
  expires_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
  created_at: string;
}

/** Create response — includes the plaintext key exactly once. */
export interface ApiKeyCreated extends ApiKeyInfo {
  key: string;
}

export async function listApiKeys(wsId: string): Promise<ApiKeyInfo[]> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/api-keys`);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to list API keys");
  }
  return resp.json();
}

export async function createApiKey(
  wsId: string,
  data: { name: string; scopes?: ApiKeyScope[]; expires_in_days?: number },
): Promise<ApiKeyCreated> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/api-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to create API key");
  }
  return resp.json();
}

export async function revokeApiKey(wsId: string, keyId: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/workspaces/${wsId}/api-keys/${keyId}`, {
    method: "DELETE",
  });
  if (!resp.ok && resp.status !== 204) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message || "Failed to revoke API key");
  }
}
