from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.jose import JsonWebKey

# OIDC provider configs (simplified - set via env or tenant settings)
PROVIDERS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "client_id": "",
        "client_secret": "",
        "scope": "openid email profile",
    },
    "azure": {
        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2/token",
        "userinfo_url": "https://graph.microsoft.com/v1.0/me",
        "client_id": "",
        "client_secret": "",
        "scope": "openid email profile",
    },
    "okta": {
        "authorize_url": "https://{domain}/oauth2/default/v1/authorize",
        "token_url": "https://{domain}/oauth2/default/v1/token",
        "userinfo_url": "https://{domain}/oauth2/default/v1/userinfo",
        "client_id": "",
        "client_secret": "",
        "scope": "openid email profile",
    },
}


def create_oauth_client(provider: str):
    cfg = PROVIDERS.get(provider)
    if not cfg:
        raise ValueError(f"Unknown provider: {provider}")
    return AsyncOAuth2Client(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        scope=cfg["scope"],
    )


def get_authorize_url(provider: str, redirect_uri: str) -> tuple[str, str]:
    client = create_oauth_client(provider)
    uri, state = client.create_authorization_url(
        PROVIDERS[provider]["authorize_url"],
        redirect_uri=redirect_uri,
    )
    return uri, state


async def verify_callback(provider: str, code: str, state: str, redirect_uri: str) -> dict:
    cfg = PROVIDERS.get(provider)
    if not cfg:
        raise ValueError(f"Unknown provider: {provider}")
    client = create_oauth_client(provider)
    token = await client.fetch_token(
        cfg["token_url"],
        authorization_response=f"{redirect_uri}?code={code}&state={state}",
    )
    userinfo = await client.get(cfg["userinfo_url"])
    return {**userinfo.json(), "provider": provider}
