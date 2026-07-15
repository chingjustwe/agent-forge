"""SSO/OIDC authentication helpers.

Built-in provider presets (Google, Microsoft) auto-fill OIDC endpoints so
admins only need to supply ``client_id`` / ``client_secret``. Custom OIDC
providers can either specify endpoints manually or use OIDC Discovery
(Phase 2) by providing an ``issuer_url``.

This module is transport-agnostic — it operates on ``SsoProvider`` config
dicts and delegates HTTP calls to ``authlib``'s ``AsyncOAuth2Client``.
"""

import logging

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.oidc.core import IDToken

logger = logging.getLogger(__name__)

# ── Built-in provider URL presets ──────────────────────────────────────
# When ``provider_type`` matches one of these keys, the corresponding URLs
# are auto-filled at create time and the admin only needs to provide
# ``client_id`` / ``client_secret``.
PROVIDER_PRESETS: dict[str, dict] = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "issuer_url": "https://accounts.google.com",
        "scopes": ["openid", "email", "profile"],
    },
    "microsoft": {
        # Microsoft URLs contain a ``{ms_tenant}`` placeholder that is
        # filled at request time (common / organizations / concrete tenant).
        "authorize_url": "https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "scopes": ["openid", "email", "profile"],
    },
}


def resolve_endpoints(provider: dict) -> dict:
    """Return the effective OIDC endpoints for a provider config dict.

    For built-in types (google/microsoft), preset URLs are used; for
    ``custom_oidc``, the URLs stored on the provider record are used.
    Microsoft URL templates are filled with ``ms_tenant`` (default
    ``"common"``).
    """
    ptype = provider.get("provider_type", "")
    ms_tenant = provider.get("ms_tenant") or "common"

    if ptype in PROVIDER_PRESETS:
        preset = PROVIDER_PRESETS[ptype]
        return {
            "authorize_url": preset["authorize_url"].format(ms_tenant=ms_tenant),
            "token_url": preset["token_url"].format(ms_tenant=ms_tenant),
            "userinfo_url": preset["userinfo_url"],
            "issuer_url": preset.get("issuer_url"),
            "scopes": preset["scopes"],
        }

    # custom_oidc — use stored URLs as-is.
    return {
        "authorize_url": provider.get("authorize_url") or "",
        "token_url": provider.get("token_url") or "",
        "userinfo_url": provider.get("userinfo_url") or "",
        "issuer_url": provider.get("issuer_url"),
        "scopes": provider.get("scopes") or ["openid", "email", "profile"],
    }


def create_oauth_client(provider: dict) -> AsyncOAuth2Client:
    """Build an ``AsyncOAuth2Client`` from a provider config dict."""
    endpoints = resolve_endpoints(provider)
    return AsyncOAuth2Client(
        client_id=provider["client_id"],
        client_secret=provider["client_secret"],
        scope=" ".join(endpoints["scopes"]),
    )


def get_authorize_url(provider: dict, redirect_uri: str, state: str) -> str:
    """Build the IdP authorization URL with the given state and redirect."""
    client = create_oauth_client(provider)
    endpoints = resolve_endpoints(provider)
    uri, _ = client.create_authorization_url(
        endpoints["authorize_url"],
        redirect_uri=redirect_uri,
        state=state,
    )
    return uri


async def exchange_code_for_token(
    provider: dict, code: str, redirect_uri: str
) -> dict:
    """Exchange an authorization code for an access token.

    Returns the token response dict (``access_token``, ``token_type``,
    optionally ``id_token``, ``refresh_token``, ``expires_in``).
    """
    client = create_oauth_client(provider)
    endpoints = resolve_endpoints(provider)
    token = await client.fetch_token(
        endpoints["token_url"],
        authorization_response=f"{redirect_uri}?code={code}",
        redirect_uri=redirect_uri,
        grant_type="authorization_code",
    )
    return token


async def fetch_userinfo(provider: dict, access_token: str) -> dict:
    """Fetch user info from the IdP userinfo endpoint.

    Returns a dict typically containing ``sub``, ``email``, ``name``,
    ``email_verified``, etc.
    """
    client = create_oauth_client(provider)
    endpoints = resolve_endpoints(provider)
    client.token = {"access_token": access_token, "token_type": "Bearer"}
    resp = await client.get(endpoints["userinfo_url"])
    resp.raise_for_status()
    return resp.json()


# ── OIDC Discovery (Phase 2) ──────────────────────────────────────────

async def discover_endpoints(issuer_url: str) -> dict:
    """Fetch OIDC Discovery metadata from ``issuer_url/.well-known/openid-configuration``.

    Returns a dict with keys: ``authorize_url``, ``token_url``,
    ``userinfo_url``, ``issuer_url``, ``jwks_uri``.

    Raises ``ValueError`` if the discovery document is invalid or missing
    required fields.
    """
    issuer = issuer_url.rstrip("/")
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(discovery_url)
        resp.raise_for_status()
        metadata = resp.json()

    required = ["authorization_endpoint", "token_endpoint", "issuer"]
    for field in required:
        if field not in metadata:
            raise ValueError(f"OIDC Discovery document missing required field: {field}")

    return {
        "authorize_url": metadata["authorization_endpoint"],
        "token_url": metadata["token_endpoint"],
        "userinfo_url": metadata.get("userinfo_endpoint", ""),
        "issuer_url": metadata["issuer"],
        "jwks_uri": metadata.get("jwks_uri", ""),
    }


# ── ID Token verification (Phase 2) ───────────────────────────────────

async def fetch_jwks(jwks_uri: str) -> dict:
    """Fetch JWKS (JSON Web Key Set) from the IdP."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        return resp.json()


async def verify_id_token(
    id_token: str,
    provider: dict,
    client_id: str | None = None,
) -> dict:
    """Verify an ID Token's signature and claims.

    Uses the provider's ``jwks_uri`` (from discovery or manual config) to
    fetch signing keys, then validates the token's signature, ``iss``,
    ``aud``, and ``exp`` claims.

    Returns the decoded claims dict (``sub``, ``email``, ``name``, etc.).

    Raises ``ValueError`` if verification fails.
    """
    endpoints = resolve_endpoints(provider)
    issuer = endpoints.get("issuer_url")
    jwks_uri = provider.get("jwks_uri") or endpoints.get("jwks_uri")
    expected_client_id = client_id or provider.get("client_id")

    if not jwks_uri:
        raise ValueError("No jwks_uri available — cannot verify ID Token")

    jwks_data = await fetch_jwks(jwks_uri)
    key_set = JsonWebKey.import_key_set(jwks_data)

    claims_options = {}
    if issuer:
        claims_options["iss"] = {"essential": True, "value": issuer}
    if expected_client_id:
        claims_options["aud"] = {"essential": True, "value": expected_client_id}

    try:
        claims = JsonWebToken(["RS256", "ES256"]).decode(
            id_token,
            key_set,
            claims_cls=IDToken,
            claims_options=claims_options,
        )
    except Exception as exc:
        raise ValueError(f"ID Token verification failed: {exc}") from exc

    return dict(claims)
