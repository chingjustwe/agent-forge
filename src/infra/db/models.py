import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, String, Integer, Float, DateTime, JSON, ForeignKey, Text, text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # P2-4: tenant-level daily token quota. 0 means unlimited (default),
    # consistent with Workspace.max_tokens_per_day semantics. server_default
    # keeps raw SQL INSERTs (which omit this column) working — required so
    # existing migration tests in test_workspace_slug.py don't break.
    max_total_tokens_per_day: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(32), ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    max_tokens_per_day: Mapped[int] = mapped_column(Integer, default=1_000_000)
    max_cost_per_day: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0.0"))
    max_cost_per_month: Mapped[float] = mapped_column(Float, default=0.0)
    archived: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[int] = mapped_column(Integer, default=0)
    # P1-3: Workspace field completion — slug/description/icon/owner_id/updated_at.
    # No DB-level unique constraint on slug (SQLite constraint migration is
    # complex); uniqueness is enforced at the application layer.
    slug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=True
    )


class QuotaUsage(Base):
    __tablename__ = "quota_usage"

    workspace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    args: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class EventLog(Base):
    __tablename__ = "events_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    event: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class OTelSettings(Base):
    __tablename__ = "otel_settings"

    workspace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    endpoint: Mapped[str] = mapped_column(String(512), default="")
    headers: Mapped[str] = mapped_column(Text, default="{}")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(32), ForeignKey("tenants.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    workspace_ids: Mapped[list] = mapped_column(JSON, default=list)
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="builtin")
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived: Mapped[int] = mapped_column(Integer, default=0)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    added_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    visibility: Mapped[str] = mapped_column(String(32), default="private")
    # private: only owner can see (default)
    # workspace: all workspace members can see
    agent_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    archived: Mapped[int] = mapped_column(Integer, default=0)  # soft-delete flag
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user / assistant / system
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ChatSessionShare(Base):
    """P3-5: per-user session sharing.

    A row (session_id, user_id) grants ``user_id`` view access to a private
    ``ChatSession`` owned by someone else. ``shared_by`` is the user who
    created the share (typically the session owner or a workspace admin).
    Composite PK (session_id, user_id) makes repeated shares idempotent —
    re-sharing with the same user is a no-op (shared_at is NOT bumped).

    Visibility matrix (``_can_see_session`` in sessions.py):
    - tenant_admin → sees everything
    - workspace_admin → sees everything in their workspace
    - owner → sees their own sessions
    - shared user (ChatSessionShare.user_id == self) → sees the session
    - otherwise → only non-private sessions
    """
    __tablename__ = "chat_session_shares"
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), primary_key=True
    )
    shared_by: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False
    )
    shared_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WorkspaceInvitation(Base):
    """P2-1: workspace-scoped invitation links.

    A workspace_admin generates a token-bearing link. A registered
    user clicks it, hits ``/api/v1/invitations/{token}/accept`` and is
    added to the workspace as a ``WorkspaceMember``. ``email=None`` means
    a generic link any logged-in user can accept.
    """
    __tablename__ = "workspace_invitations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invited_by: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AgentConfig(Base):
    """P2-2 + P3a: workspace-scoped agent configurations.

    Each agent config is bound to a workspace and references an adapter
    (``deepagents``). The ``framework`` column is the legacy name for
    ``adapter`` — kept for backward compat with existing API clients and
    tests; new code reads ``adapter`` via the Pydantic ``AgentDefinition``
    wrapper. Wave 2.5 removed ``direct_llm`` / ``adk`` / ``langgraph``.

    P3a adds structured fields (``system_prompt``, ``model``,
    ``temperature``, ``max_tokens``, ``tools``, ``guardrails``,
    ``skills``, ``hooks``, ``memory_config``) so the harness can build
    a ``HarnessContext`` without parsing free-form JSON. The legacy
    ``config`` JSON column is preserved as ``metadata`` for
    framework-specific extras not covered by the structured fields.

    Cross-workspace access is prevented by always filtering on
    ``workspace_id``.
    """
    __tablename__ = "agent_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Legacy column; ``adapter`` in Pydantic maps to this.
    framework: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    # ── P3a structured fields (added via M12 migration) ──
    system_prompt: Mapped[str] = mapped_column(Text, default="", server_default="")
    model: Mapped[str] = mapped_column(
        String(100), default="deepseek-v4-flash", server_default="deepseek-v4-flash"
    )
    temperature: Mapped[float] = mapped_column(
        Float, default=0.7, server_default="0.7"
    )
    max_tokens: Mapped[int] = mapped_column(
        Integer, default=4096, server_default="4096"
    )
    tools: Mapped[list] = mapped_column(JSON, default=list)
    guardrails: Mapped[list] = mapped_column(JSON, default=list)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    hooks: Mapped[list] = mapped_column(JSON, default=list)
    memory_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Phase 4 (M17): subagent specs (list of SubagentSpec dicts).
    # Only used when framework='deepagents'. Empty list = no subagents.
    subagents: Mapped[list] = mapped_column(JSON, default=list)
    # Phase 5 (M20): MCP servers this agent is explicitly bound to.
    # The agent gets access to EVERY tool exposed by each selected server
    # (union with ``tools``). Empty list = no MCP servers bound.
    mcp_servers: Mapped[list] = mapped_column(JSON, default=list)


class ApiKey(Base):
    """P2-3: workspace-scoped API keys.

    The plaintext key (``ap_<32 hex>``) is shown to the caller exactly once
    at creation time; the DB stores only ``key_hash`` (SHA-256 hex).
    ``key_prefix`` is the first 8 chars of the plaintext key (including the
    ``ap_`` marker) used for display in list views. Revocation is a soft
    delete (``revoked=1``) so audit history is preserved. Cross-workspace
    isolation is enforced at the application layer by always filtering on
    ``workspace_id``.
    """
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(10))
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(32), ForeignKey("tenants.id"), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CheckpointWrite(Base):
    """Phase 4c: persists LangGraph intermediate task writes.

    Rows are written by ``LangGraphCheckpointShim.aput_writes()`` and
    read back by ``aget_tuple()`` so a crash between ``aput_writes``
    and the next ``aput`` no longer loses pending writes (spec §11).
    Composite PK matches LangGraph's reference ``(thread_id, task_id,
    task_path, channel)`` tuple.
    """

    __tablename__ = "checkpoint_writes"

    session_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_path: Mapped[str] = mapped_column(String(255), primary_key=True, default="")
    channel: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MCPServer(Base):
    """P3a §6.3: persisted MCP server registration, scoped per workspace.

    Mirrors the ``mcp_servers`` table created by migration M14. ``MCPManager``
    reads/writes this table so registrations survive process restarts (the
    original P1 implementation was in-memory only and lost all servers on
    restart).
    """

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(String(20), default="http")
    auth_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_mcp_server_ws_name"),
    )


class Skill(Base):
    """Workspace-scoped, UI-writable skill (Skills layers spec, M21).

    The platform aggregates skills from three layers: ``user``
    (``skill_user_dir``) and ``project`` (``agents/skills``) are read-only
    directory sources; ``workspace`` skills live here (or in the filesystem
    backend) and are editable via the API. Field semantics mirror
    ``SkillPackage``: ``name / description / instructions / tools /
    required_memory / version``. ``instructions`` is the markdown body
    injected into the system prompt by ``PromptAssembler``.
    """

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list] = mapped_column(JSON, default=list)
    required_memory: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    created_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=True
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_skill_ws_name"),
    )


class ModelPricing(Base):
    """Per-model token pricing synced from models.dev.

    Stores input/output cost in USD per million tokens. The ``model_name``
    column holds the bare model name (e.g. ``deepseek-v4-flash``) used by the
    platform's RuntimeConfig, while ``full_id`` keeps the provider-prefixed
    ID from models.dev (e.g. ``deepseek/deepseek-v4-flash``) for traceability.
    """

    __tablename__ = "model_pricing"

    model_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    full_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    input_cost_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    output_cost_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, server_default=text("CURRENT_TIMESTAMP")
    )


class SsoProvider(Base):
    """SSO/OIDC provider configuration (Phase 1 — SSO authentication).

    Supports built-in providers (Google, Microsoft) with auto-filled URLs
    and custom OIDC providers with manually-specified endpoints.
    ``tenant_id`` NULL = global provider available to all tenants.
    """

    __tablename__ = "sso_providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)

    #租户隔离：NULL = 全局 provider（所有租户可用）
    tenant_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("tenants.id"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False)

    # Provider 类型：google | microsoft | custom_oidc
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # OAuth2 凭据
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret: Mapped[str] = mapped_column(String(500), nullable=False)

    # OIDC endpoints（google/microsoft 自动从预设填充；custom_oidc 手填）
    authorize_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    token_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    userinfo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    issuer_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Phase 2: JWKS URI for ID Token verification (auto-filled via discovery)
    jwks_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)

    scopes: Mapped[list] = mapped_column(
        JSON, default=lambda: ["openid", "email", "profile"]
    )

    # Microsoft 专用：tenant 占位（common / organizations / 具体 tenant ID）
    ms_tenant: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 行为配置
    auto_provision: Mapped[int] = mapped_column(Integer, default=1)
    default_role: Mapped[str] = mapped_column(String(32), default="member")
    enabled: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_sso_providers_tenant_slug"),
    )


class UserIdentity(Base):
    """External identity linkage — associates a local user with an SSO
    provider's ``sub`` claim. One user may have multiple identities
    (e.g. Google + company OIDC).
    """

    __tablename__ = "user_identities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    provider_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sso_providers.id"), nullable=False
    )
    # IdP 返回的 "sub" claim — 外部身份的唯一标识
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # IdP 返回的 email（审计用，可能变化）
    email_at_provider: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "provider_id", "provider_subject",
            name="uq_user_identities_provider_subject",
        ),
    )
