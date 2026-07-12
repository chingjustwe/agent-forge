# Getting Started

This guide walks you through running Agent Forge locally — the FastAPI backend and the React SPA frontend.

## Prerequisites

- **Python 3.11+**
- **Node.js 20+**
- **`uv`** (recommended) or `pip`

## 1. Backend

```bash
# Install dependencies
uv sync --dev
# or: pip install -e ".[dev]"

# Start the server
uvicorn src.main:create_app --reload --port 8000
# or: python -m src.main
```

The API is available at `http://localhost:8000/api/v1/`.

## 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

The UI is available at `http://localhost:5175`.

## 3. Configure LLM API Key

```bash
cp .env.example .env
# Edit .env and set your DeepSeek API key:
# LLM_API_KEY=sk-<your-key>
```

## 4. (Optional) Configure SMTP for Email

To send real invitation emails instead of printing to console:

```bash
# Edit .env and set SMTP credentials (e.g., Brevo free tier: 300 emails/day)
# SMTP_HOST=smtp-relay.brevo.com
# SMTP_PORT=587
# SMTP_USER=<your-brevo-account-email>
# SMTP_PASSWORD=<your-smtp-key>   # use the SMTP key, NOT the API key
# SMTP_FROM=noreply@yourdomain.com
```

Leave SMTP unset to use the console sender during development.

## 5. Create the First User

Register via the API (the server must be running first):

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"yourpassword","name":"Admin"}'
```

The first user role is `member`. To grant admin access, update the DB:

```bash
sqlite3 data/agent_platform.db "UPDATE users SET role='tenant_admin' WHERE email='admin@example.com';"
```

The SQLite database is created automatically at `data/agent_platform.db` on first start. Then log in at `http://localhost:5175`.

## What's Next?

- [Architecture](/guide/architecture) — understand the three-layer design
- [Configuration](/guide/configuration) — tune settings and integrations
- [RBAC & Permissions](/guide/rbac) — the permission model
- [API Reference](/guide/api-reference) — endpoint summary
