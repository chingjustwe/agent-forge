from contextlib import asynccontextmanager
from pathlib import Path
import secrets

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text as sa_text

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
from src.gateway.routes.hooks import router as hooks_router
from src.gateway.routes.scheduler import router as scheduler_router
from src.gateway.routes.models import router as models_router
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
            "memory_type TEXT NOT NULL DEFAULT 'episodic',"
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
            ("model", "VARCHAR(100) DEFAULT 'deepseek-v4-flash'"),
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

    # Migration M20 (Phase 5): add ``mcp_servers`` JSON column to agent_configs.
    # Declarative binding of an agent to workspace-scoped MCP servers; the
    # agent receives every tool exposed by each selected server. Empty list
    # (default) means no MCP servers bound. Idempotent: checks column list.
    if "agent_configs" in tables:
        cols = {c["name"] for c in insp.get_columns("agent_configs")}
        if "mcp_servers" not in cols:
            try:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE agent_configs ADD COLUMN mcp_servers JSON DEFAULT '[]'"
                )
            except Exception as exc:  # pragma: no cover — defensive
                print(f"[migration] M20 add mcp_servers skipped: {exc}")

    # Migration M21 (Skills layers spec): create skills table (idempotent).
    # Stores workspace-scoped, UI-writable skills (the "workspace" layer).
    # The "user" / "project" layers remain read-only directory sources.
    if "workspaces" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS skills ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "workspace_id VARCHAR(32) NOT NULL,"
            "name VARCHAR(255) NOT NULL,"
            "description TEXT DEFAULT '',"
            "instructions TEXT DEFAULT '',"
            "tools JSON DEFAULT '[]',"
            "required_memory INTEGER DEFAULT 0,"
            "version VARCHAR(32) DEFAULT '1.0',"
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "UNIQUE (workspace_id, name)"
            ")"
        )
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_skills_workspace_id "
            "ON skills (workspace_id)"
        )

    # Migration M18 (Phase 4c): checkpoint lookup index + pending-writes table.
    # The index speeds up ``load_latest`` (ORDER BY sequence DESC LIMIT 1)
    # and ``list`` (ORDER BY sequence ASC) for sessions with many checkpoints.
    # The ``checkpoint_writes`` table persists LangGraph's intermediate task
    # writes so a crash between ``aput_writes`` and the next ``aput`` no
    # longer loses pending writes (spec §11 — Phase 4c hardening).
    if "checkpoints" in tables:
        sync_conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_checkpoints_session_seq "
            "ON checkpoints (session_id, sequence DESC)"
        )
    if "workspaces" in tables:
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS checkpoint_writes ("
            "session_id VARCHAR(32) NOT NULL,"
            "checkpoint_id VARCHAR(64) NOT NULL DEFAULT '',"
            "task_id VARCHAR(64) NOT NULL,"
            "task_path VARCHAR(255) NOT NULL DEFAULT '',"
            "channel VARCHAR(64) NOT NULL,"
            "value TEXT NOT NULL,"
            "created_at DATETIME NOT NULL,"
            "PRIMARY KEY (session_id, checkpoint_id, task_id, task_path, channel)"
            ")"
        )
        # M20: add checkpoint_id column to pre-existing checkpoint_writes
        # tables (added in M18). ``ALTER TABLE … ADD COLUMN`` with
        # ``IF NOT EXISTS`` isn't supported on SQLite, so introspect.
        cols = {
            row[1]
            for row in sync_conn.exec_driver_sql("PRAGMA table_info(checkpoint_writes)")
        }
        if "checkpoint_id" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE checkpoint_writes ADD COLUMN checkpoint_id "
                "VARCHAR(64) NOT NULL DEFAULT ''"
            )
            # Rebuild the primary key to include checkpoint_id. SQLite
            # cannot ALTER a PK in place, so rename → recreate → copy.
            sync_conn.exec_driver_sql("ALTER TABLE checkpoint_writes RENAME TO checkpoint_writes_old")
            sync_conn.exec_driver_sql(
                "CREATE TABLE checkpoint_writes ("
                "session_id VARCHAR(32) NOT NULL,"
                "checkpoint_id VARCHAR(64) NOT NULL DEFAULT '',"
                "task_id VARCHAR(64) NOT NULL,"
                "task_path VARCHAR(255) NOT NULL DEFAULT '',"
                "channel VARCHAR(64) NOT NULL,"
                "value TEXT NOT NULL,"
                "created_at DATETIME NOT NULL,"
                "PRIMARY KEY (session_id, checkpoint_id, task_id, task_path, channel)"
                ")"
            )
            sync_conn.exec_driver_sql(
                "INSERT INTO checkpoint_writes "
                "(session_id, checkpoint_id, task_id, task_path, channel, value, created_at) "
                "SELECT session_id, checkpoint_id, task_id, task_path, channel, value, created_at "
                "FROM checkpoint_writes_old"
            )
            sync_conn.exec_driver_sql("DROP TABLE checkpoint_writes_old")
        # M20: channel-value blobs. LangGraph 1.2+ stores each channel's
        # value separately (keyed by version) rather than inline in the
        # checkpoint dict — the ``checkpoint_blobs`` table mirrors
        # MemorySaver's ``blobs`` dict so ``aget_tuple`` can rebuild
        # ``channel_values`` from ``channel_versions``.
        sync_conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS checkpoint_blobs ("
            "session_id VARCHAR(32) NOT NULL,"
            "channel VARCHAR(64) NOT NULL,"
            "version VARCHAR(64) NOT NULL,"
            "type VARCHAR(16) NOT NULL DEFAULT 'json',"
            "payload TEXT NOT NULL,"
            "PRIMARY KEY (session_id, channel, version)"
            ")"
        )

    # Migration M19: backfill a default agent for workspaces that have none.
    # New workspaces get one at creation time (see ``workspaces.py``); this
    # one-time backfill covers workspaces created before that landed.
    if "workspaces" in tables and "agent_configs" in tables:
        from src.gateway.routes.workspaces import (
            _DEFAULT_AGENT_NAME,
            _DEFAULT_AGENT_SYSTEM_PROMPT,
        )
        cur = sync_conn.exec_driver_sql(
            "SELECT w.id, w.owner_id FROM workspaces w "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM agent_configs a WHERE a.workspace_id = w.id"
            ")"
        )
        empty = cur.fetchall()
        for ws_id, owner_id in empty:
            sync_conn.execute(
                sa_text(
                    "INSERT INTO agent_configs "
                    "(id, workspace_id, name, framework, config, "
                    " system_prompt, model, temperature, max_tokens, "
                    " created_by, created_at, updated_at) "
                    "VALUES (:id, :ws, :name, 'deepagents', '{}', "
                    "        :sys, 'deepseek-v4-flash', 0.7, 4096, "
                    "        :owner, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": secrets.token_hex(8),
                    "ws": ws_id,
                    "name": _DEFAULT_AGENT_NAME,
                    "sys": _DEFAULT_AGENT_SYSTEM_PROMPT,
                    "owner": owner_id or "",
                },
            )
        if empty:
            print(f"[migration] M19 backfilled {len(empty)} workspace(s) with default agent")

    # Migration M22 (Wave 3 — long-term memory recall enhancement):
    # Add ``memory_type`` column to memory_records.  Distinguishes
    # "profile" records (always injected into system prompt) from
    # "episodic" records (recalled by topic query).  Old records
    # default to 'episodic' for backward compatibility.
    if "workspaces" in tables and "memory_records" in tables:
        cols = {
            row[1]
            for row in sync_conn.exec_driver_sql(
                "PRAGMA table_info(memory_records)"
            )
        }
        if "memory_type" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE memory_records ADD COLUMN memory_type "
                "TEXT NOT NULL DEFAULT 'episodic'"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_memory_type "
                "ON memory_records (scope, scope_id, memory_type)"
            )

    # Migration M23 (Wave 2.5 — remove DirectLLM): rewrite any legacy
    # ``framework='direct_llm'`` rows to ``'deepagents'``. DirectLLM was
    # deleted; _resolve_adapter also falls back to deepagents as a
    # double safety net (decision D1). This one-shot UPDATE keeps the
    # DB consistent so listings/filters show the real framework.
    if "agent_configs" in tables:
        cur = sync_conn.exec_driver_sql(
            "SELECT COUNT(*) FROM agent_configs WHERE framework = 'direct_llm'"
        )
        stale = cur.fetchone()[0]
        if stale:
            sync_conn.exec_driver_sql(
                "UPDATE agent_configs SET framework = 'deepagents' "
                "WHERE framework = 'direct_llm'"
            )
            print(
                f"[migration] M23 rewrote {stale} agent_configs row(s) "
                f"from 'direct_llm' to 'deepagents'"
            )

    # Migration M24 (Quota cost calculation — model pricing table):
    # Create ``model_pricing`` table to cache per-model token costs synced
    # from models.dev. Used by QuotaGuardrail.record_usage to compute cost.
    # ``Base.metadata.create_all`` already creates it for fresh DBs; this
    # block handles existing DBs that were created before this table existed.
    if "model_pricing" not in tables:
        sync_conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS model_pricing (
                model_name VARCHAR(255) PRIMARY KEY,
                full_id VARCHAR(255) NOT NULL,
                provider VARCHAR(100) NOT NULL,
                display_name VARCHAR(255) DEFAULT '',
                input_cost_per_mtok FLOAT DEFAULT 0.0,
                output_cost_per_mtok FLOAT DEFAULT 0.0,
                synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )



async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_schema)

    app.state.telemetry = TelemetryCollector()

    # P3a: wire the HarnessRegistry (platform-level singleton container)
    # and HarnessRuntime (sole orchestrator). Both are idempotent —
    # HarnessRegistry.create() skips already-registered tools/guardrails,
    # and set_runtime() overwrites any previous instance.
    from src.runtime.harness.registry import (
        HarnessRegistry,
        reset_registry,
        set_registry,
    )
    from src.runtime.harness.runtime import HarnessRuntime, set_runtime

    reset_registry()
    registry = HarnessRegistry.create()
    app.state.registry = registry
    # Share the exact same instance with get_registry() used by route
    # handlers — otherwise they'd lazily build a fresh (empty) registry and
    # never see persisted MCP servers / the started scheduler.
    set_registry(registry)
    app.state.runtime = HarnessRuntime(registry)
    set_runtime(app.state.runtime)
    # P3: inject runtime into scheduler and start it
    registry.scheduler.set_runtime(app.state.runtime)
    await registry.scheduler.start()

    # P3a: load persisted MCP server registrations so they survive restarts.
    await registry.mcp.load_from_db()

    # Model pricing sync: fetch from models.dev once at startup, then
    # schedule an hourly refresh. Failures are logged inside sync() and
    # never propagate — the platform can run with stale/empty pricing.
    from src.infra.telemetry.pricing import ModelPricingSync
    pricing_sync = ModelPricingSync()
    await pricing_sync.sync()
    registry.scheduler._scheduler.add_job(
        pricing_sync.sync,
        "interval",
        hours=1,
        id="model_pricing_sync",
        name="Sync model pricing from models.dev",
        replace_existing=True,
    )

    # P-model-catalog: fetch the live model list from the LLM provider's
    # /v1/models once at startup, then refresh hourly so the Agents UI
    # always reflects the real catalog (e.g. deepseek-v4-flash / -v4-pro).
    from src.infra.llm.models import fetch_available_models

    await fetch_available_models()
    registry.scheduler._scheduler.add_job(
        fetch_available_models,
        "interval",
        hours=1,
        id="model_catalog_sync",
        name="Sync model catalog from provider /v1/models",
        replace_existing=True,
    )

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
    app.include_router(hooks_router)
    app.include_router(scheduler_router)
    app.include_router(models_router)

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
