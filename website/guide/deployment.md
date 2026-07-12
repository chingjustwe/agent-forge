# Deployment

> This page is a starter scaffold. Fill in production deployment details (Docker, reverse proxy, TLS) as your setup matures.

## Running the Stack

### Backend

```bash
uv sync --dev
uvicorn src.main:create_app --host 0.0.0.0 --port 8000
```

Or as a module:

```bash
python -m src.main
```

### Frontend

Build the SPA and serve the static output, or serve it via your reverse proxy:

```bash
cd frontend
npm install
npm run build
# output in frontend/dist/
```

## Configuration in Production

Set the production values for the variables described in [Configuration](/guide/configuration), especially:

- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`
- `DATABASE_URL` (point at your production database)
- `SMTP_*` for real invitation emails

## Reverse Proxy Notes

- The API is served under `/api/v1/`.
- Chat uses SSE streaming — ensure your proxy does not buffer responses (disable proxy buffering for the chat route).
- Serve the built frontend static files and proxy `/api/v1/` to the backend.

## Future Work

- Container image / Docker Compose
- Managed PostgreSQL
- OIDC SSO wiring (currently a stub)
