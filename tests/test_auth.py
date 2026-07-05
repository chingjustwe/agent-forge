import pytest

from src.gateway.auth.jwt import create_jwt, decode_jwt
from src.gateway.auth.password import hash_password, verify_password
from src.gateway.auth.roles import has_permission, Role


class TestJWT:
    def test_create_and_decode_jwt(self):
        user = {
            "id": "user-1",
            "tenant_id": "tenant-1",
            "email": "test@example.com",
            "role": "member",
        }
        token = create_jwt(user)
        claims = decode_jwt(token)
        assert claims is not None
        assert claims["sub"] == "user-1"
        assert claims["tenant_id"] == "tenant-1"
        assert claims["email"] == "test@example.com"
        assert claims["role"] == "member"
        assert "exp" in claims
        assert "iat" in claims

    def test_jwt_does_not_contain_workspace_ids(self):
        """P0-4: JWT must NOT carry workspace_ids — query WorkspaceMember at runtime.

        Even if the caller passes ``workspace_ids`` in the user dict, the
        encoded JWT must omit it. New tokens only carry sub/tenant_id/email/role/exp/iat.
        """
        user = {
            "id": "user-1",
            "tenant_id": "tenant-1",
            "email": "test@example.com",
            "role": "member",
            "workspace_ids": ["ws-1"],  # must be ignored by create_jwt
        }
        token = create_jwt(user)
        claims = decode_jwt(token)
        assert claims is not None
        assert "workspace_ids" not in claims
        # Standard claims still present
        for key in ("sub", "tenant_id", "email", "role", "exp", "iat"):
            assert key in claims

    def test_decode_invalid_token(self):
        assert decode_jwt("invalid.token.here") is None

    def test_decode_empty_token(self):
        assert decode_jwt("") is None


class TestPassword:
    def test_hash_and_verify(self):
        pw = "my-secret-password"
        hashed = hash_password(pw)
        assert hashed != pw
        assert verify_password(pw, hashed) is True

    def test_wrong_password(self):
        hashed = hash_password("correct-pw")
        assert verify_password("wrong-pw", hashed) is False

    def test_different_hashes(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses different salts


class TestRoles:
    def test_role_hierarchy(self):
        assert has_permission("tenant_admin", "viewer") is True
        assert has_permission("member", "viewer") is True
        assert has_permission("viewer", "member") is False
        assert has_permission("viewer", "tenant_admin") is False
        assert has_permission("workspace_admin", "member") is True
        assert has_permission("tenant_admin", "workspace_admin") is True
        assert has_permission("member", "workspace_admin") is False

    def test_role_enum_values(self):
        assert Role.VIEWER.value == "viewer"
        assert Role.MEMBER.value == "member"
        assert Role.TENANT_ADMIN.value == "tenant_admin"
