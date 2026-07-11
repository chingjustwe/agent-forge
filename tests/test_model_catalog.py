"""Tests for the dynamic model catalog (src/infra/llm/models.py) and the
GET /api/v1/models route.

The catalog is fetched live from the LLM provider's /v1/models endpoint so
the Agents UI shows the real model list (e.g. deepseek-v4-flash /
deepseek-v4-pro) instead of a hardcoded one.
"""
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt
from src.infra.llm import models as model_module


@pytest.fixture(autouse=True)
def _reset_cache():
    model_module._AVAILABLE_MODELS = []
    yield
    model_module._AVAILABLE_MODELS = []


def _token() -> str:
    return create_jwt({
        "id": "u1",
        "sub": "u1",
        "tenant_id": "t1",
        "email": "u1@test.com",
        "role": "member",
    })


async def test_fetch_available_models_parses_ids(httpx_mock):
    httpx_mock.add_response(
        url="https://api.deepseek.com/v1/models",
        json={
            "object": "list",
            "data": [
                {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
                {"id": "deepseek-v4-pro", "object": "model", "owned_by": "deepseek"},
            ],
        },
    )
    models = await model_module.fetch_available_models()
    assert models == ["deepseek-v4-flash", "deepseek-v4-pro"]
    # cache + default reflect the parsed list
    assert model_module.get_available_models() == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert model_module.get_default_model() == "deepseek-v4-flash"


async def test_fetch_available_models_empty_data_still_caches_nothing(httpx_mock):
    httpx_mock.add_response(
        url="https://api.deepseek.com/v1/models",
        json={"object": "list", "data": []},
    )
    models = await model_module.fetch_available_models()
    assert models == []
    assert model_module.get_available_models() == []


async def test_fetch_available_models_network_failure_returns_empty(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    models = await model_module.fetch_available_models()
    assert models == []
    assert model_module.get_default_model() == ""


async def test_fetch_available_models_skips_entries_without_id(httpx_mock):
    httpx_mock.add_response(
        url="https://api.deepseek.com/v1/models",
        json={
            "object": "list",
            "data": [
                {"id": "deepseek-v4-flash"},
                {"object": "model"},  # missing id -> skipped
                {"id": "deepseek-v4-pro"},
            ],
        },
    )
    models = await model_module.fetch_available_models()
    assert models == ["deepseek-v4-flash", "deepseek-v4-pro"]


async def test_route_returns_catalog(monkeypatch):
    monkeypatch.setattr(
        model_module, "get_available_models", lambda: ["deepseek-v4-flash", "deepseek-v4-pro"]
    )
    monkeypatch.setattr(model_module, "get_default_model", lambda: "deepseek-v4-flash")

    from src.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/models", headers={"Authorization": f"Bearer {_token()}"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert body["default"] == "deepseek-v4-flash"


async def test_route_requires_auth(monkeypatch):
    monkeypatch.setattr(model_module, "get_available_models", lambda: [])
    monkeypatch.setattr(model_module, "get_default_model", lambda: "")

    from src.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/models")
    assert resp.status_code == 401
