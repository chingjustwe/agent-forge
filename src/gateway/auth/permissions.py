"""权限加载器：从项目根目录 permissions.yaml 读取配置，提供运行时检查。

使用方式：
    from src.gateway.auth.permissions import has_permission, get_frontend_tabs

    if has_permission("workspace_admin", "agents:write"):
        ...

    tabs = get_frontend_tabs()
"""

from functools import lru_cache
from pathlib import Path

import yaml


# 项目根目录（src/gateway/auth/permissions.py → 向上 4 层到项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "permissions.yaml"


@lru_cache(maxsize=1)
def load_permissions() -> dict:
    """加载 YAML 配置并缓存（进程生命周期内只读一次）。"""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def has_permission(user_role: str, permission: str) -> bool:
    """检查指定角色是否拥有某权限。

    tenant_admin（super_admin: true）自动通过所有权限检查。
    """
    cfg = load_permissions()
    roles = cfg.get("roles", {})
    role_def = roles.get(user_role)
    if role_def is None:
        return False
    if role_def.get("super_admin"):
        return True
    return permission in role_def.get("permissions", [])


def get_role_permissions(user_role: str) -> list[str]:
    """返回指定角色拥有的所有权限列表。

    tenant_admin 返回特殊标记 ["*"]。
    """
    cfg = load_permissions()
    roles = cfg.get("roles", {})
    role_def = roles.get(user_role)
    if role_def is None:
        return []
    if role_def.get("super_admin"):
        return ["*"]
    return list(role_def.get("permissions", []))


def get_frontend_tabs() -> dict:
    """返回前端 tab→permission 映射。"""
    return load_permissions().get("frontend_tabs", {})


def get_all_roles() -> dict:
    """返回所有角色定义（不含 frontend_tabs）。"""
    return load_permissions().get("roles", {})


def get_api_key_scopes() -> list[str]:
    """返回 API Key 可用的 scope 列表。

    自动从所有角色权限的并集生成，排除 admin 前缀的 scope
    （admin 权限不应通过 API Key 暴露）。
    始终包含 chat:write（API Key 专用 scope，不在角色权限中定义）。
    """
    config = load_permissions()
    # 1. 优先使用显式配置的 api_key_scopes
    explicit = config.get("api_key_scopes")
    if explicit:
        return explicit
    # 2. 自动推导：收集所有角色权限，排除 admin:* / members:*
    all_perms: set[str] = {"chat:write"}  # API Key 专用 scope
    for role_name, role_def in config.get("roles", {}).items():
        if role_def.get("super_admin"):
            continue
        for perm in role_def.get("permissions", []):
            if not perm.startswith("admin:") and not perm.startswith("members:"):
                all_perms.add(perm)
    return sorted(all_perms)