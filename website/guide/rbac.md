# RBAC & Permissions

Role-based access control is driven entirely by `permissions.yaml` at the project root — the single source of truth. Route handlers use `require_permission("resource:action")`, which reads from this file.

## Roles

| Role | Scope | Description |
|------|-------|-------------|
| `viewer` | Workspace | Read-only access |
| `member` | Workspace | Can chat |
| `workspace_admin` | Workspace | Full workspace management (members, settings, API keys, invitations, archive/delete) |
| `tenant_admin` | Tenant | Super admin — all permissions, all workspaces |

## Permission Matrix

```
                                  viewer  member  ws_admin  tenant_admin
──────────────────────────────────────────────────────────────────────────
sessions:read                       ✓       ✓        ✓           ✓
sessions:write                      ✗       ✓        ✓           ✓
sessions:delete                     ✗       ✗        ✓           ✓
agents:read                         ✓       ✓        ✓           ✓
agents:write                        ✗       ✗        ✓           ✓
quota:read                          ✓       ✓        ✓           ✓
quota:write                         ✗       ✗        ✓           ✓
invitations:read                    ✗       ✗        ✓           ✓
invitations:write                   ✗       ✗        ✓           ✓
api_keys:read                       ✗       ✗        ✓           ✓
api_keys:write                      ✗       ✗        ✓           ✓
settings:read                       ✗       ✗        ✓           ✓
settings:write                      ✗       ✗        ✓           ✓
members:read                        ✗       ✗        ✓           ✓
members:write                       ✗       ✗        ✓           ✓
workspace:delete                    ✗       ✗        ✓           ✓
workspace:archive                   ✗       ✗        ✓           ✓
admin:workspaces:read               ✗       ✗     ✓(scoped)      ✓
admin:users:read                    ✗       ✗     ✓(scoped)      ✓
admin:audit:read                    ✗       ✗     ✓(scoped)      ✓
admin:usage:read                    ✗       ✗     ✓(scoped)      ✓
admin:workspaces:write              ✗       ✗        ✗           ✓
admin:users:write                   ✗       ✗        ✗           ✓
admin:tenant:write                  ✗       ✗        ✗           ✓
──────────────────────────────────────────────────────────────────────────
Admin sidebar visible               ✗       ✗        ✓           ✓
```

**Scoped admin** means `workspace_admin` can see the Admin section, but data is limited to the workspaces they manage. `tenant_admin` sees all data across all workspaces.

## Adding a new permission

1. Add the permission name to `permissions.yaml` under the appropriate role(s).
2. Use `Depends(require_permission("new:perm"))` in the route handler.
3. If the permission controls a frontend tab, add it to `frontend_tabs` in `permissions.yaml`.
