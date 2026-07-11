"""Dynamic model catalog.

At startup (and then hourly) we fetch the list of available models from the
configured LLM provider's OpenAI-compatible ``/v1/models`` endpoint. The
Agents UI populates its model dropdown from this *real* catalog instead of a
hardcoded list, so vendor-specific model names (e.g. ``deepseek-v4-flash`` /
``deepseek-v4-pro``) show up correctly regardless of which DeepSeek deployment
is configured.

The catalog is cached in-process. Failures are logged and never crash the app
— we keep the last-known list (or an empty list on the very first call) so the
UI degrades gracefully rather than raising.
"""
from __future__ import annotations

import logging
from typing import List

import httpx

from src.infra.settings import settings

logger = logging.getLogger(__name__)

# In-process cache. asyncio is single-threaded so a plain list is safe.
_AVAILABLE_MODELS: List[str] = []


async def fetch_available_models() -> List[str]:
    """Fetch the model catalog from the provider's ``/v1/models`` endpoint.

    Updates the in-process cache and returns the parsed model id list. On any
    failure, logs and returns the previously cached list (empty on the very
    first call). Never raises.
    """
    global _AVAILABLE_MODELS
    url = f"{settings.llm_base_url.rstrip('/')}/v1/models"
    headers = (
        {"Authorization": f"Bearer {settings.llm_api_key}"}
        if settings.llm_api_key
        else {}
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        data = payload.get("data") or []
        models = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
        if models:
            _AVAILABLE_MODELS = models
            logger.info(
                "Fetched %d model(s) from provider /v1/models", len(models)
            )
        else:
            logger.warning("Provider /v1/models returned no models: %s", url)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to fetch model catalog from %s: %s", url, exc)
    return get_available_models()


def get_available_models() -> List[str]:
    """Return the cached model catalog (may be empty if never fetched)."""
    return list(_AVAILABLE_MODELS)


def get_default_model() -> str:
    """Return the default model — first available, else empty string."""
    models = get_available_models()
    return models[0] if models else ""


# Last-resort fallback used when the catalog has never been fetched (e.g.
# before the startup sync completes, or the provider was unreachable). Points
# at a real model from the current DeepSeek deployment so a model-less agent
# never sends a non-existent ``deepseek-v4-flash`` to the API.
_FALLBACK_DEFAULT_MODEL = "deepseek-v4-flash"


def resolve_default_model() -> str:
    """Dynamic default model: live catalog first, hardcoded fallback second."""
    return get_default_model() or _FALLBACK_DEFAULT_MODEL
