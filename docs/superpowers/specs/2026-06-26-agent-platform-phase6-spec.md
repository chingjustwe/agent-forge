# Remote Agent Platform — Phase 6 Spec: Admin UI & Audit Log

> **Scope:** Full admin interface for tenant management, user management, workspace administration, quota oversight, and audit log viewer. All governance features from Phases 2-5 are now manageable through the UI.

---

## 1. Tenant Management (tenant_admin only)

```
GET    /api/v1/admin/tenants
       → 200 [{id, name, domain, user_count, workspace_count, created_at}]

PUT    /api/v1/admin/tenants/{id}
       → Body: {name, domain, settings}
       → 200 {tenant}
```

## 2. User Management (tenant_admin only)

```
GET    /api/v1/admin/users?search=&role=&workspace_id=
       → 200 [{id, email, name, role, workspaces, last_login, created_at}]

PUT    /api/v1/admin/users/{id}
       → Body: {role, workspace_ids}
       → 200 {user}

DELETE /api/v1/admin/users/{id}
       → 204

POST   /api/v1/admin/users/invite
       → Body: {email, role, workspace_id}
       → 201 {invitation}
```

## 3. Workspace Management (tenant_admin + workspace_owner)

```
GET    /api/v1/admin/workspaces
       → 200 [{id, name, member_count, agent_count, owner, created_at}]

PUT    /api/v1/admin/workspaces/{id}
       → Body: {name, settings, quota}
       → 200 {workspace}

DELETE /api/v1/admin/workspaces/{id}
       → 204 (archive, not hard delete)
```

## 4. Usage & Quota (tenant_admin + workspace_owner)

```
GET    /api/v1/admin/usage?tenant_id=&since=&until=
       → 200 {total_requests, total_tokens, total_cost, by_workspace: [...]}

PUT    /api/v1/admin/workspaces/{id}/quota
       → Body: {max_tokens_per_day, max_cost_per_month}
       → 200 {quota}
```

## 5. Audit Log Viewer

### Data

Audit logs are collected automatically from Phase 2 onward (see Phase 2 §4 — Audit Middleware). Every state mutation creates an `audit_log` row with user, action, target, and timestamp.

### API

```
GET /api/v1/admin/audit?tenant_id=&action=&user_id=&since=&until=&limit=&offset=
  → 200 {items: [{id, action, user, target, details, created_at}], total}

GET /api/v1/workspaces/{id}/audit
  → 200 (same structure, scoped to workspace)
```

### Frontend

- **Audit log table**: Sortable, filterable by action/user/date range
- **Detail panel**: Expand row to see before/after diff
- **Workspace-scoped view**: Members see only their workspace's audit log

## 6. Frontend: Admin Pages

| Page | Route | Access |
|------|-------|--------|
| Admin Dashboard | `/admin` | tenant_admin |
| User Management | `/admin/users` | tenant_admin |
| Workspace Management | `/admin/workspaces` | tenant_admin, workspace_owner |
| Audit Log | `/admin/audit` | tenant_admin (all), workspace_owner (own) |
| Usage Dashboard | `/admin/usage` | tenant_admin, workspace_owner |
| Quota Config | `/admin/workspaces/{id}/quota` | tenant_admin, workspace_owner |

## 7. Directory Additions

```
frontend/src/pages/
├── AdminDashboard.tsx
├── AdminUsers.tsx
├── AdminWorkspaces.tsx
├── AdminAuditLog.tsx
└── AdminUsage.tsx
```

## 8. Acceptance Criteria

```
[✓] pytest tests/ -v passes
[✓] Tenant admin can view/edit users, workspaces, roles
[✓] Tenant admin can invite new users
[✓] Workspace admin can view workspace audit log
[✓] Every state mutation creates audit log entry (verified since Phase 2)
[✓] Audit log viewer is filterable by action/user/date
[✓] Quota enforcement blocks requests exceeding daily token limit
[✓] Admin sees usage breakdown by workspace
[✓] Existing Phase 2 auth works — no re-login needed
[✓] Admin Dashboard shows tenant overview stats
[✓] Workspace archive removes workspace from active list
```
