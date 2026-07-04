import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, String, Integer, Float, DateTime, JSON, ForeignKey, Text, text
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
    - workspace_admin/owner → sees everything in their workspace
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

    A workspace_admin/owner generates a token-bearing link. A registered
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
    """P2-2: workspace-scoped agent configurations.

    Each agent config is bound to a workspace and references a framework
    (``direct_llm`` / ``adk`` / ``langgraph``). The ``config`` JSON holds
    framework-specific settings (model, system_prompt, temperature, tools,
    ...). Cross-workspace access is prevented by always filtering on
    ``workspace_id``.
    """
    __tablename__ = "agent_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    framework: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )


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
