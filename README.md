# Agent Platform

A multi-tenant AI agent platform with RBAC, observability, quota management, and admin dashboard.

## Architecture

```
Frontend (React/Vite)  →  Backend (FastAPI)  →  Agent Runtime (stub)
                                │
                    ┌───────────┼───────────┐
                    │           │           │
               SQLite/DB   OTel Export   LLM API
```

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- `uv` (recommended) or `pip`

### 1. Backend

```bash
# Install dependencies
uv sync --dev
# or: pip install -e ".[dev]"

# Start server
uvicorn src.main:create_app --reload --port 8000
# or: python -m src.main
```

API available at `http://localhost:8000/api/v1/`

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI available at `http://localhost:5173`

### 3. Configure LLM API Key

```bash
# Create .env file from example (or export the variable)
cp .env.example .env
# Edit .env and set your DeepSeek API key:
# LLM_API_KEY=sk-<your-key>
```

### 4. First User

Register via API (server must be running first):

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"yourpassword","name":"Admin"}'
```

The first user role is `member`. To enable admin access, update the DB:

```bash
sqlite3 data/agent_platform.db "UPDATE users SET role='tenant_admin' WHERE email='admin@example.com';"
```

> **Note:** The SQLite database is located at `data/agent_platform.db`. It is created automatically when the server starts for the first time.

Then log in at `http://localhost:5173`.

## Admin Routes

| Path | Description |
|------|-------------|
| `/admin/dashboard` | Tenant overview, usage summary |
| `/admin/users` | User CRUD + invite |
| `/admin/workspaces` | Workspace management |
| `/admin/audit` | Audit log viewer |
| `/admin/usage` | Usage statistics per workspace |

## API Overview

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/auth/register` | Register |
| `POST /api/v1/auth/login` | Login |
| `GET /api/v1/health` | Health check |
| `POST /api/v1/chat` | Chat (SSE streaming) |
| `GET /api/v1/workspaces/{id}/observability/*` | Metrics, traces |
| `GET/PUT /api/v1/workspaces/{id}/quota` | Quota management |
| `GET /api/v1/admin/*` | Admin API |

## Development

```bash
# Run all tests
python -m pytest tests/ -v

# Run with live reload (both terminals)
python -m src.main                  # backend
cd frontend && npm run dev          # frontend
```
