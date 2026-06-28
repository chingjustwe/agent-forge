from enum import Enum

from fastapi import HTTPException, Request


class Role(str, Enum):
    VIEWER = "viewer"
    MEMBER = "member"
    WORKSPACE_ADMIN = "workspace_admin"
    WORKSPACE_OWNER = "workspace_owner"
    TENANT_ADMIN = "tenant_admin"


ROLE_HIERARCHY: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.MEMBER: 1,
    Role.WORKSPACE_ADMIN: 2,
    Role.WORKSPACE_OWNER: 3,
    Role.TENANT_ADMIN: 4,
}


def has_permission(user_role: str, min_role: str) -> bool:
    user_level = ROLE_HIERARCHY.get(Role(user_role), -1)
    min_level = ROLE_HIERARCHY.get(Role(min_role), -1)
    return user_level >= min_level


async def check_role(request: Request, min_role: str):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not has_permission(user.get("role", ""), min_role):
        raise HTTPException(status_code=403, detail="Forbidden")
