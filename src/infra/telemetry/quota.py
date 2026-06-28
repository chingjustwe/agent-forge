from datetime import date as date_type
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.engine import async_session
from src.infra.db.models import Workspace


class GuardrailResult:
    def __init__(self, passed: bool, action: str, reason: str = ""):
        self.passed = passed
        self.action = action
        self.reason = reason


class QuotaGuardrail:
    async def check(self, workspace_id: str) -> GuardrailResult:
        if not workspace_id:
            return GuardrailResult(passed=True, action="allow")
        today = date_type.today().isoformat()
        async with async_session() as session:
            ws = await session.get(Workspace, workspace_id)
            if not ws or ws.max_tokens_per_day == 0:
                return GuardrailResult(passed=True, action="allow")

            row = (await session.execute(
                text("SELECT tokens_used FROM quota_usage WHERE workspace_id = :ws_id AND date = :today"),
                {"ws_id": workspace_id, "today": today},
            )).one_or_none()

            tokens_used = row.tokens_used if row else 0

            if tokens_used >= ws.max_tokens_per_day:
                return GuardrailResult(
                    passed=False,
                    action="block",
                    reason=f"Daily token quota exceeded ({tokens_used}/{ws.max_tokens_per_day})",
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

            return {
                "max_tokens_per_day": ws.max_tokens_per_day if ws else 1_000_000,
                "max_cost_per_month": ws.max_cost_per_month if ws else 0.0,
                "tokens_used": tokens_used,
                "cost_today": cost_today,
            }
