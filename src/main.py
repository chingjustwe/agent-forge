from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.gateway.routes.chat import router as chat_router
from src.gateway.routes.auth import router as auth_router
from src.gateway.routes.me import router as me_router
from src.gateway.routes.workspaces import router as workspaces_router
from src.gateway.routes.admin import router as user_router, admin_router
from src.gateway.routes.audit import router as audit_router
from src.gateway.routes.observability import router as observability_router
from src.gateway.routes.quota import router as quota_router
from src.gateway.routes.settings import router as settings_router
from src.gateway.routes.sessions import router as sessions_router
from src.gateway.routes.invitations import router as invitations_router
from src.gateway.routes.agents import router as agents_router
from src.gateway.routes.api_keys import router as api_keys_router
from src.gateway.routes.tools import router as tools_router
from src.gateway.routes.mcp import router as mcp_router
from src.gateway.routes.skills import router as skills_router
from src.gateway.routes.memory import router as memory_router
from src.gateway.routes.guardrails import router as guardrails_router
from src.gateway.routes.scheduler import router as scheduler_router
from src.gateway.middleware.auth import AuthMiddleware
from src.gateway.middleware.audit import AuditMiddleware
from src.infra.db.engine import engine
from src.infra.db.models import Base
from src.infra.telemetry.collector import TelemetryCollector


def _migrate_schema(sync_conn):
    """Run inside sync executor: add missing columns, seed defaults."""
    import sqlalchemy
    from sqlalchemy import inspect
    insp = inspect(sync_conn)
    tables = insp.get_table_names()

    # Migration: add is_default column if missing
    if "workspaces" in tables:
        cols = {c["name"] for c in insp.get_columns("workspaces")}
        if "is_default" not in cols:
            sync_conn.exec_driver_sql("ALTER TABLE workspaces ADD COLUMN is_default INTEGER DEFAULT 0")

        # Migration M4: P1-3 — add slug / description / icon / owner_id / updated_at.
        # Idempotent: each column is checked individually before ADD COLUMN.
        # NOTE: SQLite's ALTER TABLE ADD COLUMN only accepts constant defaults,
        # so updated_at is added without a SQL default; SQLAlchemy's
        # ``onupdate`` hook sets the timestamp on subsequent updates.
        new_columns = [
            ("slug", "VARCHAR(100)"),
            ("description", "VARCHAR(500)"),
            ("icon", "VARCHAR(255)"),
            ("owner_id", "VARCHAR(32)"),
            ("updated_at", "DATETIME"),
        ]
        for col_name, col_type in new_columns:
            if col_name not in cols:
                sync_conn.exec_driver_sql(
                    f"ALTER TABLE workspaces ADD COLUMN {col_name} {col_type}"
                )

    # Seed: create a default workspace for each tenant
    if "tenants" in tables and "workspaces" in tables:
        rows = sync_conn.exec_driver_sql("SELECT id FROM tenants").fetchall()
        for (tid,) in rows:
            existing = sync_conn.exec_driver_sql(
                "SELECT id FROM workspaces WHERE tenant_id = ? AND is_default = 1",
                (tid,),
            ).fetchone()
            if not existing:
                import uuid
                ws_id = uuid.uuid4().hex[:16]
                sync_conn.exec_driver_sql(
                    "INSERT INTO workspaces (id, tenant_id, name, created_at, settings, max_tokens_per_day, max_cost_per_month, archived, is_default) "
                    "VALUES (?, ?, ?, datetime('now'), '{}', 1000000, 0.0, 0, 1)",
                    (ws_id, tid, "Default"),
                )

    # Migration M5: P1-3 — backfill slug from name for existing workspaces.
    # Runs after M4 (which adds the slug column) and after the default-workspace
    # seed so newly seeded "Default" workspaces also get a slug. Idempotent:
    # only rows with slug IS NULL are touched.
    if "workspaces" in tables:
        from src.utils.slugify import slugify as _slugify

        null_rows = sync_conn.exec_driver_sql(
            "SELECT id, tenant_id, name FROM workspaces WHERE slug IS NULL"
        ).fetchall()
        # Resolve tenant-local conflicts by appending -2 / -3 / ...
        for ws_id, tenant_id, name in null_rows:
            base = _slugify(name)
            candidate = base
            suffix = 2
            while True:
                existing = sync_conn.exec_driver_sql(
                    "SELECT id FROM workspaces WHERE tenant_id = ? AND slug = ? AND id != ?",
                    (tenant_id, candidate, ws_id),
                ).fetchone()
                if existing is None:
                    break
                candidate = f"{base}-{suffix}"
                suffix += 1
            sync_conn.exec_driver_sql(
                "UPDATE workspaces SET slug = ? WHERE id = ?",
                (candidate, ws_id),
            )

    # Migration M5b: P1-3 follow-up — enforce tenant-local slug uniqueness at
    # the DB layer. SQLite allows multiple NULLs in a UNIQUE index, so
    # workspaces with no slug don't conflict. Idempotent: CREATE INDEX IF NOT
    # EXISTS. Runs after M5 ensures all existing rows have a unique slug.
    if "workspaces" in tables:
        try:
            sync_conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_workspaces_tenant_slug ON workspaces(tenant_id, slug)"
            )
        except Exception as exc:  # pragma: no cover — defensive
            # If duplicates somehow slipped in, log and skip so the app still boots.
            print(f"[migration] M5b unique-index creation skipped: {exc}")

    # Migration M6: P2-4 — Tenant 表加 max_total_tokens_per_day 字段。
    # 0 表示不限制（默认值），与 Workspace.max_tokens_per_day 语义一致。
    # Idempotent: checks column existence before ADD COLUMN.
    if "tenants" in tables:
        cols = {c["name"] for c in insp.get_columns("tenants")}
        if "max_total_tokens_per_day" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE tenants ADD COLUMN max_total_tokens_per_day INTEGER DEFAULT 0"
            )

    # Migration: create workspace_members table if not exists
    if "users" in tables and "workspaces" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS workspace_members ("
            "workspace_id VARCHAR(32) NOT NULL REFERENCES workspaces(id),"
            "user_id VARCHAR(32) NOT NULL REFERENCES users(id),"
            "role VARCHAR(32) DEFAULT 'member' NOT NULL,"
            "joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "added_by VARCHAR(32) REFERENCES users(id),"
            "last_active_at DATETIME,"
            "PRIMARY KEY (workspace_id, user_id)"
            ")"
        )

    # Migration: backfill workspace_members from users.workspace_ids
    if "users" in tables:
        import json
        role_map = {
            "tenant_admin": "workspace_owner",
            "workspace_owner": "workspace_owner",
            "workspace_admin": "workspace_admin",
            "member": "member",
            "viewer": "member",
        }
        user_rows = sync_conn.exec_driver_sql(
            "SELECT id, role, workspace_ids FROM users"
        ).fetchall()
        for user_id, user_role, workspace_ids_value in user_rows:
            if not workspace_ids_value:
                continue
            if isinstance(workspace_ids_value, str):
                try:
                    workspace_ids = json.loads(workspace_ids_value)
                except (TypeError, ValueError):
                    continue
            elif isinstance(workspace_ids_value, list):
                workspace_ids = workspace_ids_value
            else:
                continue
            if not workspace_ids:
                continue
            member_role = role_map.get(user_role, "member")
            for ws_id in workspace_ids:
                sync_conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO workspace_members "
                    "(workspace_id, user_id, role, joined_at) "
                    "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                    (ws_id, user_id, member_role),
                )

    # Migration M3: create chat_sessions + chat_messages tables (idempotent).
    # Uses CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS so running
    # the migration multiple times is safe.
    if "workspaces" in tables and "users" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS chat_sessions ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "workspace_id VARCHAR(32) NOT NULL REFERENCES workspaces(id),"
            "owner_id VARCHAR(32) NOT NULL REFERENCES users(id),"
            "title VARCHAR(255) DEFAULT 'New Chat',"
            "visibility VARCHAR(32) DEFAULT 'private',"
            "agent_name VARCHAR(100),"
            "archived INTEGER DEFAULT 0,"
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS chat_messages ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "session_id VARCHAR(32) NOT NULL REFERENCES chat_sessions(id),"
            "role VARCHAR(20) NOT NULL,"
            "content TEXT NOT NULL,"
            "tokens INTEGER DEFAULT 0,"
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_sessions_workspace_id "
            "ON chat_sessions (workspace_id)"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_sessions_owner_id "
            "ON chat_sessions (owner_id)"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_session_id "
            "ON chat_messages (session_id)"
        )

        # Migration M3b (P3-5): create chat_session_shares table (idempotent).
        # Composite PK (session_id, user_id) makes repeated shares idempotent.
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS chat_session_shares ("
            "session_id VARCHAR(32) NOT NULL REFERENCES chat_sessions(id),"
            "user_id VARCHAR(32) NOT NULL REFERENCES users(id),"
            "shared_by VARCHAR(32) NOT NULL REFERENCES users(id),"
            "shared_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "PRIMARY KEY (session_id, user_id)"
            ")"
        )

    # Migration M7 (P2-1): create workspace_invitations table (idempotent).
    # Stores per-workspace invitation links with a random token; an invitee
    # accepts via /api/v1/invitations/{token}/accept to join as a member.
    if "workspaces" in tables and "users" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS workspace_invitations ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "workspace_id VARCHAR(32) NOT NULL REFERENCES workspaces(id),"
            "email VARCHAR(255),"
            "role VARCHAR(32) DEFAULT 'member' NOT NULL,"
            "token VARCHAR(64) NOT NULL UNIQUE,"
            "invited_by VARCHAR(32) NOT NULL REFERENCES users(id),"
            "expires_at DATETIME NOT NULL,"
            "accepted_at DATETIME,"
            "accepted_by VARCHAR(32) REFERENCES users(id),"
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_workspace_invitations_workspace_id "
            "ON workspace_invitations (workspace_id)"
        )
        sync_conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_workspace_invitations_token "
            "ON workspace_invitations (token)"
        )

    # Migration M8 (P2-2): create agent_configs table (idempotent).
    # Stores per-workspace agent configurations (framework + free-form JSON
    # config). Cross-workspace isolation is enforced at the application layer.
    if "workspaces" in tables and "users" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS agent_configs ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "workspace_id VARCHAR(32) NOT NULL REFERENCES workspaces(id),"
            "name VARCHAR(100) NOT NULL,"
            "framework VARCHAR(50) NOT NULL,"
            "config JSON,"
            "created_by VARCHAR(32) NOT NULL REFERENCES users(id),"
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_agent_configs_workspace_id "
            "ON agent_configs (workspace_id)"
        )

    # Migration M9 (P2-3): create api_keys table (idempotent).
    # Stores workspace-scoped API keys. Only ``key_hash`` (SHA-256 hex) is
    # persisted — the plaintext key is shown to the caller exactly once at
    # creation time. Revocation is a soft delete (revoked=1).
    if "workspaces" in tables and "users" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS api_keys ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "workspace_id VARCHAR(32) NOT NULL REFERENCES workspaces(id),"
            "name VARCHAR(100) NOT NULL,"
            "key_prefix VARCHAR(10),"
            "key_hash VARCHAR(128) NOT NULL UNIQUE,"
            "scopes JSON,"
            "created_by VARCHAR(32) NOT NULL REFERENCES users(id),"
            "expires_at DATETIME,"
            "last_used_at DATETIME,"
            "revoked INTEGER DEFAULT 0,"
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_api_keys_workspace_id "
            "ON api_keys (workspace_id)"
        )
        sync_conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_api_keys_key_hash "
            "ON api_keys (key_hash)"
        )

    # M11: Merge workspace_owner into workspace_admin
    try:
        sync_conn.exec_driver_sql(
            "UPDATE workspace_members SET role = 'workspace_admin' "
            "WHERE role = 'workspace_owner'"
        )
    except Exception:
        pass

    # Migration M13 (P3a-P1): create checkpoints table (idempotent).
    # Stores per-session conversation snapshots for crash recovery.
    if "workspaces" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS checkpoints ("
            "session_id VARCHAR(32) NOT NULL,"
            "sequence INTEGER NOT NULL,"
            "messages TEXT NOT NULL,"
            "tool_state TEXT NOT NULL,"
            "agent_id VARCHAR(32) NOT NULL,"
            "metadata TEXT NOT NULL DEFAULT '{}',"
            "created_at DATETIME NOT NULL,"
            "PRIMARY KEY (session_id, sequence)"
            ")"
        )

    # Migration M14 (P3a-P1): create mcp_servers table (idempotent).
    # Stores per-workspace MCP server configurations.
    if "workspaces" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS mcp_servers ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "name VARCHAR(100) NOT NULL,"
            "workspace_id VARCHAR(32) NOT NULL REFERENCES workspaces(id),"
            "endpoint TEXT NOT NULL,"
            "transport VARCHAR(20) DEFAULT 'http',"
            "auth_token TEXT,"
            "enabled INTEGER DEFAULT 1,"
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "UNIQUE (workspace_id, name)"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_mcp_servers_workspace_id "
            "ON mcp_servers (workspace_id)"
        )

    # Migration M16 (P3b-P3): create scheduled_jobs table.
    # Stores cron-based agent invocation jobs.
    if "workspaces" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS scheduled_jobs ("
            "id TEXT NOT NULL PRIMARY KEY,"
            "workspace_id TEXT NOT NULL,"
            "agent_id TEXT NOT NULL,"
            "name TEXT NOT NULL,"
            "cron TEXT NOT NULL,"
            "input_messages TEXT NOT NULL DEFAULT '[]',"
            "enabled INTEGER NOT NULL DEFAULT 1,"
            "created_at TEXT NOT NULL,"
            "last_run_at TEXT,"
            "next_run_at TEXT"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_jobs_workspace "
            "ON scheduled_jobs (workspace_id)"
        )

    # Migration M15 (P3b-P2): create memory_records table + FTS5 index.
    # Stores long-term agent memories scoped to session/user/workspace/agent.
    if "workspaces" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS memory_records ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "scope VARCHAR(20) NOT NULL,"
            "scope_id VARCHAR(32) NOT NULL,"
            "key TEXT,"
            "content TEXT NOT NULL,"
            "metadata TEXT NOT NULL DEFAULT '{}',"
            "created_at DATETIME NOT NULL,"
            "expires_at DATETIME"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_memory_scope "
            "ON memory_records (scope, scope_id)"
        )
        # FTS5 virtual table for full-text search on content
        sync_conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts USING fts5("
            "content, content='memory_records', content_rowid='rowid'"
            ")"
        )

    # Migration M12 (P3a): extend agent_configs with structured fields.
    # The legacy ``framework`` + ``config`` JSON columns are kept as-is
    # for backward compat with existing API clients. New structured
    # fields let the harness build a HarnessContext without parsing
    # free-form JSON. Idempotent: each column is checked individually.
    if "agent_configs" in tables:
        cols = {c["name"] for c in insp.get_columns("agent_configs")}
        new_columns = [
            ("system_prompt", "TEXT DEFAULT ''"),
            ("model", "VARCHAR(100) DEFAULT 'deepseek-chat'"),
            ("temperature", "FLOAT DEFAULT 0.7"),
            ("max_tokens", "INTEGER DEFAULT 4096"),
            ("tools", "JSON"),
            ("guardrails", "JSON"),
            ("skills", "JSON"),
            ("hooks", "JSON"),
            ("memory_config", "JSON"),
        ]
        for col_name, col_type in new_columns:
            if col_name not in cols:
                try:
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE agent_configs ADD COLUMN {col_name} {col_type}"
                    )
                except Exception as exc:  # pragma: no cover — defensive
                    print(f"[migration] M12 add {col_name} skipped: {exc}")

    # Migration M17 (Phase 4): add ``subagents`` JSON column to agent_configs.
    # Stores a list of SubagentSpec dicts; only used when framework='deepagents'.
    # Empty list (default) means no subagents. Idempotent: checks column list.
    if "agent_configs" in tables:
        cols = {c["name"] for c in insp.get_columns("agent_configs")}
        if "subagents" not in cols:
            try:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE agent_configs ADD COLUMN subagents JSON DEFAULT '[]'"
                )
            except Exception as exc:  # pragma: no cover — defensive
                print(f"[migration] M17 add subagents skipped: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_schema)

    app.state.telemetry = TelemetryCollector()

    # P3a: wire the HarnessRegistry (platform-level singleton container)
    # and HarnessRuntime (sole orchestrator). Both are idempotent —
    # HarnessRegistry.create() skips already-registered tools/guardrails,
    # and set_runtime() overwrites any previous instance.
    from src.runtime.harness.registry import HarnessRegistry, reset_registry
    from src.runtime.harness.runtime import HarnessRuntime, set_runtime

    reset_registry()
    registry = HarnessRegistry.create()
    app.state.registry = registry
    app.state.runtime = HarnessRuntime(registry)
    set_runtime(app.state.runtime)
    # P3: inject runtime into scheduler and start it
    registry.scheduler.set_runtime(app.state.runtime)
    await registry.scheduler.start()

    yield

    # Graceful shutdown: release any held resources (MCP connections,
    # scheduler, etc.).
    await registry.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Platform", lifespan=lifespan)

    app.include_router(chat_router)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(workspaces_router)
    app.include_router(user_router)
    app.include_router(admin_router)
    app.include_router(audit_router)
    app.include_router(observability_router)
    app.include_router(quota_router)
    app.include_router(settings_router)
    app.include_router(sessions_router)
    app.include_router(invitations_router)
    app.include_router(agents_router)
    app.include_router(api_keys_router)
    app.include_router(tools_router)
    app.include_router(mcp_router)
    app.include_router(skills_router)
    app.include_router(memory_router)
    app.include_router(guardrails_router)
    app.include_router(scheduler_router)

    app.add_middleware(AuthMiddleware)
    app.add_middleware(AuditMiddleware)

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:create_app", host="0.0.0.0", port=8000, reload=True)
