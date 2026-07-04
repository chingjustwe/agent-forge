from datetime import date as date_type
from sqlalchemy import text

from src.infra.db.engine import async_session
from src.infra.db.models import Tenant, Workspace


class GuardrailResult:
    def __init__(self, passed: bool, action: str, reason: str = "", scope: str = ""):
        self.passed = passed
        self.action = action
        self.reason = reason
        # P2-4: scope distinguishes which quota layer blocked the request.
        # Values: "" / "workspace" / "tenant". Backward-compatible default
        # is empty string so existing callers are unaffected.
        self.scope = scope


class QuotaGuardrail:
    async def check(self, workspace_id: str) -> GuardrailResult:
        if not workspace_id:
            return GuardrailResult(passed=True, action="allow")
        today = date_type.today().isoformat()
        async with async_session() as session:
            ws = await session.get(Workspace, workspace_id)
            if not ws:
                return GuardrailResult(passed=True, action="allow")

            # 1. Workspace-level check (original logic). max=0 means unlimited.
            if ws.max_tokens_per_day > 0:
                row = (await session.execute(
                    text("SELECT tokens_used FROM quota_usage WHERE workspace_id = :ws_id AND date = :today"),
                    {"ws_id": workspace_id, "today": today},
                )).one_or_none()

                tokens_used = row.tokens_used if row else 0

                if tokens_used >= ws.max_tokens_per_day:
                    return GuardrailResult(
                        passed=False,
                        action="block",
                        scope="workspace",
                        reason=f"Workspace daily quota exceeded ({tokens_used}/{ws.max_tokens_per_day})",
                    )

            # 2. Tenant-level check (P2-4). Aggregates quota_usage across all
            # workspaces under the same tenant for today. max=0 means unlimited.
            tenant = await session.get(Tenant, ws.tenant_id)
            if tenant and tenant.max_total_tokens_per_day > 0:
                tenant_used = (await session.execute(
                    text(
                        "SELECT COALESCE(SUM(qu.tokens_used), 0) "
                        "FROM quota_usage qu "
                        "JOIN workspaces w ON qu.workspace_id = w.id "
                        "WHERE w.tenant_id = :tenant_id AND qu.date = :today"
                    ),
                    {"tenant_id": ws.tenant_id, "today": today},
                )).scalar() or 0

                if tenant_used >= tenant.max_total_tokens_per_day:
                    return GuardrailResult(
                        passed=False,
                        action="block",
                        scope="tenant",
                        reason=f"Tenant daily quota exceeded ({tenant_used}/{tenant.max_total_tokens_per_day})",
                    )

            return GuardrailResult(passed=True, action="allow")

    async def record_usage(self, workspace_id: str, tokens: int, cost: float = 0.0) -> None:
        today = date_type.today().isoformat()
        async with async_session() as session:
            await session.execute(
                text("""
                    INSERT INTO quota_usage (workspace_id, date, tokens_used, cost)
                    VALUES (:ws_id, :today, :tokens, :cost)
                    ON CONFLICT(workspace_id, date) DO UPDATE SET
                        tokens_used = tokens_used + :tokens,
                        cost = cost + :cost
                """),
                {"ws_id": workspace_id, "today": today, "tokens": tokens, "cost": cost},
            )
            await session.commit()

    async def get_usage(self, workspace_id: str) -> dict:
        today = date_type.today().isoformat()
        async with async_session() as session:
            ws = await session.get(Workspace, workspace_id)

            row = (await session.execute(
                text("SELECT tokens_used, cost FROM quota_usage WHERE workspace_id = :ws_id AND date = :today"),
                {"ws_id": workspace_id, "today": today},
            )).one_or_none()

            tokens_used = row.tokens_used if row else 0
            cost_today = row.cost if row else 0.0

            # P2-4: tenant-level usage. Aggregates quota_usage across all
            # workspaces under the same tenant for today (returns 0 if the
            # workspace has no tenant or aggregation yields no rows).
            tenant_max_tokens = 0
            tenant_tokens_used = 0
            if ws:
                tenant = await session.get(Tenant, ws.tenant_id)
                if tenant:
                    tenant_max_tokens = tenant.max_total_tokens_per_day
                    tenant_tokens_used = (await session.execute(
                        text(
                            "SELECT COALESCE(SUM(qu.tokens_used), 0) "
                            "FROM quota_usage qu "
                            "JOIN workspaces w ON qu.workspace_id = w.id "
                            "WHERE w.tenant_id = :tenant_id AND qu.date = :today"
                        ),
                        {"tenant_id": ws.tenant_id, "today": today},
                    )).scalar() or 0

            return {
                "max_tokens_per_day": ws.max_tokens_per_day if ws else 1_000_000,
                "max_cost_per_month": ws.max_cost_per_month if ws else 0.0,
                "tokens_used": tokens_used,
                "cost_today": cost_today,
                "tenant_max_tokens_per_day": tenant_max_tokens,
                "tenant_tokens_used": tenant_tokens_used,
            }
