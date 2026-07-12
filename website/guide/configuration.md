# Configuration

Agent Forge is configured through environment variables, loaded by Pydantic Settings (`src/infra/settings.py`).

## LLM Provider

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_API_KEY` | OpenAI-compatible API key (DeepSeek) | — |
| `LLM_BASE_URL` | API base URL | DeepSeek endpoint |
| `LLM_MODEL` | Default model name | — |

## Database

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLAlchemy async URL | SQLite (`data/agent_platform.db`) |

## Email (SMTP)

Leave unset to use the console email sender during development.

| Variable | Description |
|----------|-------------|
| `SMTP_HOST` | SMTP server host |
| `SMTP_PORT` | SMTP port (typically 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password / key (use the SMTP key, not the API key) |
| `SMTP_FROM` | From address |

See `.env.example` for provider-specific configs (Brevo, Gmail, Resend).

## OpenTelemetry Export

Per-workspace OTel export settings are managed at
`GET/PUT /api/v1/workspaces/{id}/settings`. These control trace/metric export
targets for observability integrations.
