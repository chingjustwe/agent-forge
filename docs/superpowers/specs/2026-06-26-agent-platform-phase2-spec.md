# Remote Agent Platform — Phase 2 Spec: Multi-Tenant + SSO + RBAC

> **Scope:** Add organization/workspace/user hierarchy, OIDC SSO login, JWT session handling, role-based access control, and tenant-aware UI. All Phase 1 functionality continues to work when authenticated.

---

## 1. Data Model

### Tenant

```python
class Tenant:
    id: str                          # UUID
    name: str                        # "Acme Corp"
    domain: str                      # "acme.com" (SSO auto-discovery)
    created_at: datetime
    settings: dict = {}              # SSO provider config, feature flags
```

### Workspace

```python
class Workspace:
    id: str                          # UUID
    tenant_id: str                   # FK → Tenant
    name: str                        # "AI Assistants"
    created_at: datetime
    settings: dict = {}              # Per-workspace agent config, quota
```

### User

```python
class User:
    id: str                          # UUID
    tenant_id: str                   # FK → Tenant
    email: str
    name: str
    role: Literal["tenant_admin", "workspace_owner", "workspace_admin", "member", "viewer"]
    workspace_ids: list[str]         # Memberships
    auth_provider: str               # "oidc" | "builtin" | "saml"
    created_at: datetime
```

### AuditLog

```python
class AuditLog:
    id: str                          # UUID
    tenant_id: str
    workspace_id: str | None
    user_id: str
    action: str                      # "workspace.create", "user.role_change", ...
    target_type: str                 # "workspace", "user", "tool", "guardrail"
    target_id: str
    details: dict = {}               # Before/after state diff
    ip_address: str
    created_at: datetime
```

Audit log is written by middleware (see §5) on every state-mutating request. Recorded from Phase 2 onward — no retroactive backfill for earlier phases.

### Role Hierarchy

| Role | Level | Scope |
|------|-------|-------|
| `tenant_admin` | Platform | Full tenant control, all workspaces |
| `workspace_owner` | Workspace | Own workspace, invite members |
| `workspace_admin` | Workspace | Manage agents & tools in workspace |
| `member` | Workspace | Use agents in workspace |
| `viewer` | Workspace | View logs, usage, cannot run agents |

## 2. Database Setup

### ORM & Engine

```python
# src/infra/db/engine.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

engine = create_async_engine("sqlite+aiosqlite:///./agent_platform.db")
async_session = async_sessionmaker(engine, expire_on_commit=False)
```

### Session Dependency

```python
# src/infra/db/session.py
async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session
```

### Auto-create Tables

In `src/main.py` lifespan:

```python
@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
```

### ORM Models

`Tenant`, `Workspace`, `User` are defined as SQLAlchemy declarative models under `Base`. All tables auto-created on startup via `create_all()`. No Alembic in Phase 2 — schema evolves by adding new models in later phases.

### Migration Strategy

Each phase adds new models to `Base.metadata`. `create_all()` is idempotent — it creates missing tables without altering existing ones. When schema changes are needed (add column, change type), a manual SQL migration is written in `sql/` and applied in the phase that introduces the change.

## 3. Auth Flow

### OIDC SSO (Primary)

```
1. User → Browser → GET /api/v1/auth/login → redirect to IdP (Google/Azure/Okta)
2. IdP → Browser → GET /api/v1/auth/callback?code=...
3. Gateway verifies token via Authlib
4. Extract email domain → lookup Tenant → create/get User
5. Issue JWT (exp: 24h, claims: user_id, tenant_id, role, workspace_ids)
6. Set httpOnly cookie + return session token in response body
7. Browser stores token, sends as Authorization: Bearer <jwt>
```

### Built-in Auth (Fallback)

```
POST /api/v1/auth/register → {email, password, name}
POST /api/v1/auth/login → {email, password} → JWT

Password hashed with bcrypt.
Built-in users are scoped to a "default" tenant (single-tenant mode).
```

### JWT Structure

```json
{
  "sub": "user-uuid",
  "tenant_id": "tenant-uuid",
  "email": "user@acme.com",
  "role": "member",
  "workspace_ids": ["ws-1", "ws-2"],
  "exp": 1712345678
}
```

## 3. RBAC Enforcement

### Middleware

```python
# gateway/middleware/auth.py

class AuthMiddleware:
    async def __call__(self, request, call_next):
        token = extract_jwt(request)
        if not token and request.url.path not in PUBLIC_ROUTES:
            return JSONResponse(401, {"error": "unauthorized"})

        request.state.user = decode_jwt(token)
        request.state.tenant_id = user["tenant_id"]
        response = await call_next(request)
        return response
```

### Public Routes

```
GET  /api/v1/health
GET  /api/v1/auth/login
GET  /api/v1/auth/callback
POST /api/v1/auth/login     (built-in)
POST /api/v1/auth/register  (built-in)
```

### Permission Check

```python
def require_role(min_role: str):
    """Decorator: tenant_admin > workspace_owner > workspace_admin > member > viewer"""
    def decorator(handler):
        async def wrapper(request, *args, **kwargs):
            user = request.state.user
            if not has_permission(user["role"], min_role):
                return JSONResponse(403, {"error": "forbidden"})
            return await handler(request, *args, **kwargs)
        return wrapper
```

### Audit Middleware

Every state-mutating POST/PUT/DELETE request is logged automatically:

```python
# gateway/middleware/audit.py

class AuditMiddleware:
    MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    SKIP_PATHS = {"/api/v1/health", "/api/v1/auth/login", "/api/v1/auth/callback"}

    async def __call__(self, request, call_next):
        if request.method in self.MUTATION_METHODS and request.url.path not in self.SKIP_PATHS:
            response = await call_next(request)
            if response.status_code < 400:
                await write_audit_log(
                    tenant_id=request.state.tenant_id,
                    user_id=request.state.user["sub"],
                    action=f"{request.method.lower()}.{request.url.path}",
                    ip_address=request.client.host,
                )
            return response
        return await call_next(request)
```

Acceptance: every user/workspace/tool/agent mutation after Phase 2 creates an audit_log row.

## 5. API Surface (new endpoints)

### Auth

```
GET  /api/v1/auth/login?provider=google
     → 302 redirect to IdP

GET  /api/v1/auth/callback?code=...
     → 302 to frontend with cookie + redirect

POST /api/v1/auth/login
     → Body: {email, password}
     → 200 {token, user}

POST /api/v1/auth/register
     → Body: {email, password, name}
     → 201 {token, user}

POST /api/v1/auth/logout
     → 200 {status: "ok"}
```

### User/Workspace (tenant admin only)

```
GET    /api/v1/workspaces
       → 200 [{id, name, member_count, created_at}]

POST   /api/v1/workspaces
       → Body: {name}
       → 201 {workspace}

GET    /api/v1/workspaces/{id}/members
       → 200 [{user_id, email, name, role}]

POST   /api/v1/workspaces/{id}/members
       → Body: {email, role}
       → 201 {membership}

DELETE /api/v1/workspaces/{id}/members/{user_id}
       → 204

GET    /api/v1/users/me
       → 200 {id, email, name, role, workspaces}

GET    /api/v1/admin/users
       → 200 [{id, email, name, role, workspace_count}]
       (tenant_admin only)
```

## 6. Frontend Changes

- **Login page**: IdP selector (Google / Azure / Okta) + email/password form
- **Workspace switcher**: Dropdown in header, filters chat context
- **User menu**: Profile, settings, logout
- **Admin page** (tenant_admin): User list, workspace CRUD, member management
- **Role-aware UI**: Viewer sees chat result but no "Run" button; Member can run but not configure

## 7. Directory Additions

```
src/gateway/auth/
├── __init__.py
├── jwt.py                    ← JWT encode/decode/middleware
├── oidc.py                   ← OIDC SSO flow (Authlib)
├── password.py               ← bcrypt hashing + built-in auth
└── roles.py                  ← Role enum + permission check
src/gateway/middleware/
├── __init__.py
└── audit.py                  ← Audit middleware (mutation logging)
src/gateway/routes/
├── auth.py                   ← /api/v1/auth/* endpoints
├── workspaces.py             ← /api/v1/workspaces/* endpoints
└── admin.py                  ← /api/v1/admin/* endpoints (tenant_admin)
src/infra/db/
├── __init__.py
├── engine.py                 ← SQLAlchemy async engine + session factory
├── session.py                ← get_db dependency
└── models.py                 ← Tenant, Workspace, User, AuditLog ORM models
```

## 8. Acceptance Criteria

```
[✓] pytest tests/ -v passes
[✓] GET /api/v1/health returns 200 without auth
[✓] POST /api/v1/chat returns 401 without token
[✓] POST /api/v1/auth/register → login → POST /api/v1/chat works
[✓] POST /api/v1/workspaces creates workspace (tenant_admin only)
[✓] Viewer role sees "chat results" but cannot POST /api/v1/chat
[✓] Workspace switcher appears in header, switches chat context
[✓] Admin page allows user management
[✓] OIDC SSO redirects to IdP and back (manual test with Google/Azure)
[✓] Creating a workspace inserts an audit_log row
[✓] Adding a workspace member inserts an audit_log row
[✓] GET /api/v1/health does NOT create an audit_log row
```
