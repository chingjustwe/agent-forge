"""Expose the dynamically-fetched model catalog to the frontend."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.infra.llm.models import get_available_models, get_default_model

router = APIRouter()


@router.get("/api/v1/models")
async def list_models(request: Request):
    """Return the live model catalog fetched from the LLM provider.

    Mirrors the auth pattern used by other non-workspace-scoped endpoints
    (``/api/v1/me/...``): requires a logged-in user, else 401. The catalog
    itself is the same for every user (single configured provider).
    """
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )
    return {
        "models": get_available_models(),
        "default": get_default_model(),
    }
