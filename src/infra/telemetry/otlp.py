import json

import httpx


class OTelExporter:
    def __init__(self, enabled: bool = False, endpoint: str = "", headers: dict | None = None):
        self.enabled = enabled
        self.endpoint = endpoint
        self.headers = headers or {}

    async def export_span(self, span_data: dict) -> None:
        if not self.enabled or not self.endpoint:
            return
        payload = {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "scope": {"name": "agent-platform"},
                            "spans": [span_data],
                        }
                    ],
                }
            ]
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(
                    self.endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json", **self.headers},
                )
            except httpx.HTTPError:
                pass

    @classmethod
    def from_settings(cls, settings: dict) -> "OTelExporter":
        return cls(
            enabled=bool(settings.get("enabled", False)),
            endpoint=str(settings.get("endpoint", "")),
            headers=dict(settings.get("headers", {})),
        )
