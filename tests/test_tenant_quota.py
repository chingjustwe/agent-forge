"""P2-4: 配额继承（tenant → workspace）测试。

覆盖：
- Tenant 模型：max_total_tokens_per_day 字段存在且默认 0
- Migration M6：字段已添加到表结构
- QuotaGuardrail.check：workspace / tenant 两层独立检查
- QuotaGuardrail.get_usage：返回 tenant 级信息
- QuotaGuardrail.record_usage：写入后 tenant used 增加
- admin 路由：tenant 配额查看/更新
"""
from datetime import date as date_type

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import Tenant, Workspace
from src.infra.telemetry.quota import QuotaGuardrail


# ─── 模型 & 迁移 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_model_has_quota_field():
    """Tenant 模型应包含 max_total_tokens_per_day 字段，默认 0（DB DEFAULT 在 INSERT 后生效）。"""
    t = Tenant(id="t-model", name="M", domain="t-model.test")
    assert hasattr(t, "max_total_tokens_per_day")
    # SQLAlchemy mapped_column default 是 INSERT 时的 DB 默认值，flush 后才落到对象上。
    async with async_session() as session:
        session.add(t)
        await session.flush()
        await session.refresh(t)
        assert t.max_total_tokens_per_day == 0
        await session.rollback()


@pytest.mark.asyncio
async def test_tenant_model_accepts_quota_value():
    """Tenant.max_total_tokens_per_day 可被赋值为正整数。"""
    t = Tenant(id="t-model-2", name="M", domain="t-model-2.test", max_total_tokens_per_day=50000)
    assert t.max_total_tokens_per_day == 50000


@pytest.mark.asyncio
async def test_migration_m6_column_exists():
    """迁移 M6：tenants 表中应存在 max_total_tokens_per_day 列。"""
    async with async_session() as session:
        result = await session.execute(
            text("PRAGMA table_info(tenants)")
        )
        cols = {row[1] for row in result.all()}
    assert "max_total_tokens_per_day" in cols


# ─── QuotaGuardrail.check ────────────────────────────────────────────────────


async def _seed_tenant_with_workspace(
    tenant_id: str,
    ws_id: str,
    ws_max_tokens: int = 0,
    tenant_max_tokens: int = 0,
) -> None:
    """创建 tenant + workspace（含配额字段），不创建 quota_usage 行。"""
    async with async_session() as session:
        t = await session.get(Tenant, tenant_id)
        if not t:
            session.add(
                Tenant(
                    id=tenant_id,
                    name=f"T {tenant_id}",
                    domain=f"{tenant_id}.test",
                    max_total_tokens_per_day=tenant_max_tokens,
                )
            )
            await session.flush()
        else:
            # 更新现有 tenant 的配额以适配当前测试
            t.max_total_tokens_per_day = tenant_max_tokens
        ws = await session.get(Workspace, ws_id)
        if not ws:
            session.add(
                Workspace(
                    id=ws_id,
                    tenant_id=tenant_id,
                    name=f"WS {ws_id}",
                    max_tokens_per_day=ws_max_tokens,
                )
            )
        else:
            ws.max_tokens_per_day = ws_max_tokens
        await session.commit()


async def _seed_usage(ws_id: str, tokens: int, day: str | None = None) -> None:
    """直接写入 quota_usage 行（绕过 record_usage 以便精确控制）。"""
    today = day or date_type.today().isoformat()
    async with async_session() as session:
        await session.execute(
            text(
                "INSERT INTO quota_usage (workspace_id, date, tokens_used, cost) "
                "VALUES (:ws_id, :today, :tokens, 0.0) "
                "ON CONFLICT(workspace_id, date) DO UPDATE SET tokens_used = :tokens"
            ),
            {"ws_id": ws_id, "today": today, "tokens": tokens},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_check_no_workspace_id_allows():
    """空 workspace_id 应直接放行。"""
    guardrail = QuotaGuardrail()
    result = await guardrail.check("")
    assert result.passed is True
    assert result.action == "allow"


@pytest.mark.asyncio
async def test_check_workspace_unconfigured_allows():
    """workspace max=0（不限）+ tenant max=0（不限）→ allow。"""
    await _seed_tenant_with_workspace("t-ws-free", "ws-free", ws_max_tokens=0, tenant_max_tokens=0)
    guardrail = QuotaGuardrail()
    result = await guardrail.check("ws-free")
    assert result.passed is True
    assert result.action == "allow"


@pytest.mark.asyncio
async def test_check_workspace_exceeded_blocks_with_scope():
    """workspace 超限 → block，scope='workspace'。"""
    await _seed_tenant_with_workspace(
        "t-ws-ex", "ws-ex", ws_max_tokens=1000, tenant_max_tokens=0
    )
    await _seed_usage("ws-ex", 1500)
    guardrail = QuotaGuardrail()
    result = await guardrail.check("ws-ex")
    assert result.passed is False
    assert result.action == "block"
    assert result.scope == "workspace"
    assert "1500" in result.reason and "1000" in result.reason


@pytest.mark.asyncio
async def test_check_tenant_exceeded_blocks_with_scope():
    """tenant 超限但 workspace 未超 → block，scope='tenant'。"""
    await _seed_tenant_with_workspace(
        "t-ten-ex", "ws-ten-ex", ws_max_tokens=0, tenant_max_tokens=2000
    )
    await _seed_usage("ws-ten-ex", 2500)
    guardrail = QuotaGuardrail()
    result = await guardrail.check("ws-ten-ex")
    assert result.passed is False
    assert result.action == "block"
    assert result.scope == "tenant"
    assert "2500" in result.reason and "2000" in result.reason


@pytest.mark.asyncio
async def test_check_workspace_priority_over_tenant():
    """workspace 超限优先于 tenant（先检查 workspace，scope='workspace'）。"""
    await _seed_tenant_with_workspace(
        "t-both", "ws-both", ws_max_tokens=500, tenant_max_tokens=2000
    )
    await _seed_usage("ws-both", 800)  # 同时超 workspace(500) 和 tenant(2000)... 这里 tenant 没超
    # 实际上 800 > 500(workspace) 但 < 2000(tenant)，所以应该是 workspace block
    guardrail = QuotaGuardrail()
    result = await guardrail.check("ws-both")
    assert result.passed is False
    assert result.scope == "workspace"


@pytest.mark.asyncio
async def test_check_workspace_exhausted_first_then_tenant_blocks_other():
    """两个 workspace 共享 tenant 配额：ws1 用满后 ws2 也被 block（scope='tenant'）。

    场景：tenant 配额 1000，ws1 用了 800，ws2 用了 300 → ws2 检查时
    tenant 累计 1100 > 1000 → block scope='tenant'。
    """
    await _seed_tenant_with_workspace(
        "t-shared", "ws-shared-1", ws_max_tokens=0, tenant_max_tokens=1000
    )
    # 第二个 workspace 同 tenant
    async with async_session() as session:
        ws2 = await session.get(Workspace, "ws-shared-2")
        if not ws2:
            session.add(
                Workspace(
                    id="ws-shared-2",
                    tenant_id="t-shared",
                    name="WS shared 2",
                    max_tokens_per_day=0,
                )
            )
            await session.commit()
    await _seed_usage("ws-shared-1", 800)
    await _seed_usage("ws-shared-2", 300)
    guardrail = QuotaGuardrail()
    result = await guardrail.check("ws-shared-2")
    assert result.passed is False
    assert result.scope == "tenant"
    # tenant 累计 1100
    assert "1100" in result.reason and "1000" in result.reason


@pytest.mark.asyncio
async def test_check_workspace_below_limit_tenant_below_limit_allows():
    """workspace 和 tenant 都未超限 → allow。"""
    await _seed_tenant_with_workspace(
        "t-ok", "ws-ok", ws_max_tokens=5000, tenant_max_tokens=10000
    )
    await _seed_usage("ws-ok", 500)  # 都未超
    guardrail = QuotaGuardrail()
    result = await guardrail.check("ws-ok")
    assert result.passed is True
    assert result.action == "allow"


@pytest.mark.asyncio
async def test_check_nonexistent_workspace_allows():
    """不存在的 workspace_id 应放行（向后兼容原有逻辑）。"""
    guardrail = QuotaGuardrail()
    result = await guardrail.check("ws-nonexistent-xyz")
    assert result.passed is True


# ─── QuotaGuardrail.get_usage ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_usage_returns_tenant_fields():
    """get_usage 返回的 dict 应包含 tenant_max_tokens_per_day 和 tenant_tokens_used。"""
    await _seed_tenant_with_workspace(
        "t-get", "ws-get", ws_max_tokens=5000, tenant_max_tokens=20000
    )
    await _seed_usage("ws-get", 1200)
    guardrail = QuotaGuardrail()
    usage = await guardrail.get_usage("ws-get")
    assert usage["max_tokens_per_day"] == 5000
    assert usage["tokens_used"] == 1200
    assert usage["tenant_max_tokens_per_day"] == 20000
    assert usage["tenant_tokens_used"] == 1200  # 只有一个 ws


@pytest.mark.asyncio
async def test_get_usage_tenant_aggregates_multiple_workspaces():
    """get_usage 的 tenant_tokens_used 应聚合同 tenant 多个 workspace 的用量。"""
    await _seed_tenant_with_workspace(
        "t-agg", "ws-agg-1", ws_max_tokens=0, tenant_max_tokens=20000
    )
    async with async_session() as session:
        ws2 = await session.get(Workspace, "ws-agg-2")
        if not ws2:
            session.add(
                Workspace(
                    id="ws-agg-2",
                    tenant_id="t-agg",
                    name="WS agg 2",
                    max_tokens_per_day=0,
                )
            )
            await session.commit()
    await _seed_usage("ws-agg-1", 700)
    await _seed_usage("ws-agg-2", 300)
    guardrail = QuotaGuardrail()
    usage = await guardrail.get_usage("ws-agg-1")
    assert usage["tenant_max_tokens_per_day"] == 20000
    assert usage["tenant_tokens_used"] == 1000  # 700 + 300


# ─── QuotaGuardrail.record_usage ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_usage_increases_tenant_used():
    """record_usage 写入后，tenant 级聚合 used 应增加。"""
    await _seed_tenant_with_workspace(
        "t-rec", "ws-rec", ws_max_tokens=0, tenant_max_tokens=5000
    )
    guardrail = QuotaGuardrail()
    await guardrail.record_usage("ws-rec", 400, cost=0.0)
    usage = await guardrail.get_usage("ws-rec")
    assert usage["tenant_tokens_used"] == 400
    assert usage["tokens_used"] == 400


# ─── admin 路由：tenant 配额管理 ─────────────────────────────────────────────


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


def _tenant_admin_token(tenant_id: str = "t-admin-quota") -> str:
    return create_jwt({
        "id": "admin-p2-4",
        "sub": "admin-p2-4",
        "tenant_id": tenant_id,
        "email": "admin-p2-4@test.com",
        "role": "tenant_admin",
    })


def _member_token(tenant_id: str = "t-admin-quota") -> str:
    return create_jwt({
        "id": "member-p2-4",
        "sub": "member-p2-4",
        "tenant_id": tenant_id,
        "email": "member-p2-4@test.com",
        "role": "member",
    })


@pytest.mark.asyncio
async def test_admin_list_tenants_includes_quota_field(app):
    """list_tenants 响应应包含 max_total_tokens_per_day 字段。"""
    # 先确保 tenant 存在
    async with async_session() as session:
        t = await session.get(Tenant, "t-admin-quota")
        if not t:
            session.add(
                Tenant(
                    id="t-admin-quota",
                    name="Admin Quota Tenant",
                    domain="t-admin-quota.test",
                    max_total_tokens_per_day=12345,
                )
            )
            await session.commit()
        else:
            t.max_total_tokens_per_day = 12345
            await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/tenants",
            headers={"Authorization": f"Bearer {_tenant_admin_token()}"},
        )
        assert resp.status_code == 200
        tenants = resp.json()
        target = next((t for t in tenants if t["id"] == "t-admin-quota"), None)
        assert target is not None
        assert target["max_total_tokens_per_day"] == 12345


@pytest.mark.asyncio
async def test_admin_get_tenant_quota(app):
    """GET /api/v1/admin/tenants/{tenant_id}/quota 返回 tenant 配额信息。"""
    async with async_session() as session:
        t = await session.get(Tenant, "t-get-quota")
        if not t:
            session.add(
                Tenant(
                    id="t-get-quota",
                    name="Get Quota",
                    domain="t-get-quota.test",
                    max_total_tokens_per_day=99999,
                )
            )
            await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/tenants/t-get-quota/quota",
            headers={"Authorization": f"Bearer {_tenant_admin_token('t-get-quota')}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_total_tokens_per_day"] == 99999
        assert "tenant_tokens_used" in data


@pytest.mark.asyncio
async def test_admin_update_tenant_quota(app):
    """PATCH /api/v1/admin/tenants/{tenant_id}/quota 更新 max_total_tokens_per_day。"""
    async with async_session() as session:
        t = await session.get(Tenant, "t-update-quota")
        if not t:
            session.add(
                Tenant(
                    id="t-update-quota",
                    name="Update Quota",
                    domain="t-update-quota.test",
                    max_total_tokens_per_day=0,
                )
            )
            await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/admin/tenants/t-update-quota/quota",
            headers={"Authorization": f"Bearer {_tenant_admin_token('t-update-quota')}"},
            json={"max_total_tokens_per_day": 88888},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_total_tokens_per_day"] == 88888

    # 验证落库
    async with async_session() as session:
        t = await session.get(Tenant, "t-update-quota")
        assert t.max_total_tokens_per_day == 88888


@pytest.mark.asyncio
async def test_admin_update_tenant_quota_forbidden_for_member(app):
    """非 tenant_admin 调 PATCH 应 403。"""
    async with async_session() as session:
        t = await session.get(Tenant, "t-update-quota-2")
        if not t:
            session.add(
                Tenant(
                    id="t-update-quota-2",
                    name="Update Quota 2",
                    domain="t-update-quota-2.test",
                    max_total_tokens_per_day=0,
                )
            )
            await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/admin/tenants/t-update-quota-2/quota",
            headers={"Authorization": f"Bearer {_member_token('t-update-quota-2')}"},
            json={"max_total_tokens_per_day": 100},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_update_tenant_via_put_endpoint(app):
    """PUT /api/v1/admin/tenants/{tenant_id} 也应支持更新 max_total_tokens_per_day。"""
    async with async_session() as session:
        t = await session.get(Tenant, "t-put-quota")
        if not t:
            session.add(
                Tenant(
                    id="t-put-quota",
                    name="Put Quota",
                    domain="t-put-quota.test",
                    max_total_tokens_per_day=0,
                )
            )
            await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            "/api/v1/admin/tenants/t-put-quota",
            headers={"Authorization": f"Bearer {_tenant_admin_token('t-put-quota')}"},
            json={"name": "Put Quota Updated", "max_total_tokens_per_day": 55555},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_total_tokens_per_day"] == 55555


@pytest.mark.asyncio
async def test_admin_get_tenant_quota_not_found(app):
    """GET 不存在的 tenant 配额 → 404。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/tenants/no-such-tenant/quota",
            headers={"Authorization": f"Bearer {_tenant_admin_token('no-such-tenant')}"},
        )
        assert resp.status_code == 404
