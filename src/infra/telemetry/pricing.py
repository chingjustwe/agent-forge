"""Model pricing sync from models.dev.

Fetches the full model catalogue from ``https://models.dev/api.json`` and
caches per-model input/output token costs (USD per million tokens) in the
``model_pricing`` table. ``get_cost()`` is called by the chat stream to
compute the cost of each request for quota tracking.

Sync runs once at startup and then hourly via APScheduler (wired in
``main.py`` lifespan). Network failures are logged and silently ignored —
stale pricing is better than blocking the chat path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from src.infra.db.engine import async_session

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"


class ModelPricingSync:
    """Sync model pricing from models.dev into the local DB."""

    async def sync(self) -> int:
        """Fetch models.dev and upsert all models into ``model_pricing``.

        Returns the number of models upserted. On network error, logs a
        warning and returns 0 without raising.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(MODELS_DEV_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("Failed to sync model pricing from models.dev: %s", exc)
            return 0

        now = datetime.now(timezone.utc).isoformat()
        count = 0
        async with async_session() as session:
            for provider_key, provider_data in data.items():
                models = provider_data.get("models", {})
                for full_id, model_info in models.items():
                    cost = model_info.get("cost") or {}
                    input_cost = float(cost.get("input", 0) or 0)
                    output_cost = float(cost.get("output", 0) or 0)
                    # bare name: strip provider prefix if present
                    model_name = full_id.split("/", 1)[1] if "/" in full_id else full_id
                    display_name = model_info.get("name", "") or model_name

                    await session.execute(
                        text("""
                            INSERT INTO model_pricing
                                (model_name, full_id, provider, display_name,
                                 input_cost_per_mtok, output_cost_per_mtok, synced_at)
                            VALUES
                                (:model_name, :full_id, :provider, :display_name,
                                 :input_cost, :output_cost, :synced_at)
                            ON CONFLICT(model_name) DO UPDATE SET
                                full_id = :full_id,
                                provider = :provider,
                                display_name = :display_name,
                                input_cost_per_mtok = :input_cost,
                                output_cost_per_mtok = :output_cost,
                                synced_at = :synced_at
                        """),
                        {
                            "model_name": model_name,
                            "full_id": full_id,
                            "provider": provider_key,
                            "display_name": display_name,
                            "input_cost": input_cost,
                            "output_cost": output_cost,
                            "synced_at": now,
                        },
                    )
                    count += 1
            await session.commit()

        logger.info("Synced %d model pricing records from models.dev", count)
        return count

    async def get_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Compute USD cost for a request.

        Looks up ``model`` (bare name, e.g. ``deepseek-v4-flash``) in the
        ``model_pricing`` table. Returns 0.0 if the model is not found or
        has no pricing data.
        """
        if not model or (input_tokens == 0 and output_tokens == 0):
            return 0.0
        async with async_session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT input_cost_per_mtok, output_cost_per_mtok "
                        "FROM model_pricing WHERE model_name = :model"
                    ),
                    {"model": model},
                )
            ).one_or_none()

        if not row:
            return 0.0

        return (
            input_tokens / 1_000_000 * row.input_cost_per_mtok
            + output_tokens / 1_000_000 * row.output_cost_per_mtok
        )
