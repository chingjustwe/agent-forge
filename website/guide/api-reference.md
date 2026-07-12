# API Reference

All routes are prefixed with `/api/v1/`. Authentication uses JWT bearer tokens.

## Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/auth/register` | Register a new user |
| `POST` | `/api/v1/auth/login` | Login (returns JWT) |
| `POST` | `/api/v1/auth/logout` | Logout |
| `GET` | `/api/v1/auth/invite/{token}` | Get invite details |
| `POST` | `/api/v1/auth/invite/{token}/accept` | Accept workspace invite |

## Core

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/health` | Health check |
| `POST` | `/api/v1/chat` | Chat (SSE streaming) |

## Workspaces

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET/PUT` | `/api/v1/workspaces/{id}/quota` | Quota management |
| `GET/PUT` | `/api/v1/workspaces/{id}/settings` | OTel export settings |
| `GET` | `/api/v1/workspaces/{id}/observability/*` | Traces, metrics, errors |

## Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/admin/*` | Admin API (users, workspaces, audit, usage) |

> Permission enforcement for these endpoints is defined in `permissions.yaml`. See [RBAC & Permissions](/guide/rbac).
