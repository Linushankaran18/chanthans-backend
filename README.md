# Studio CMS Backend

FastAPI backend for the photography studio website and admin dashboard (v1: auth + hero image management).

```
Public website --GET /api/v1/hero-images--> FastAPI
React admin ----/api/v1/admin/* (JWT)-----> FastAPI --> Supabase PostgreSQL (metadata + URLs)
                                                    \-> Cloudflare R2 (image files)
```

Stack: Python 3.12+, FastAPI, async SQLAlchemy 2 (asyncpg / aiosqlite), Alembic, Pydantic v2, boto3 (R2), PyJWT, argon2.

Layout: `app/api` (routes) -> `app/services` (business logic) -> `app/repositories` (queries); `app/core` (config, DB, security, exceptions), `app/dependencies` (FastAPI DI), `alembic/` (migrations), `scripts/` (admin creation).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # use requirements.txt for production only
cp .env.example .env                  # then edit
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` enables strict checks and hides `/docs`. |
| `DATABASE_URL` | required | Postgres URL. Plain `postgresql://` is normalized to `postgresql+asyncpg://`. |
| `JWT_SECRET` | dev placeholder | Signing key. In production must be >= 32 chars and not a default, otherwise the app refuses to start. |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token lifetime. |
| `R2_ACCOUNT_ID` | | Cloudflare account id (used to build the endpoint). |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | | R2 API token credentials. |
| `R2_BUCKET_NAME` | | Bucket name. |
| `R2_ENDPOINT_URL` | built from account id | Override, e.g. `https://<account>.r2.cloudflarestorage.com`. |
| `R2_PUBLIC_URL` | | Public base URL of the bucket; image URL = `R2_PUBLIC_URL/<key>`. |
| `MAX_UPLOAD_SIZE_MB` | `10` | Upload limit (HTTP 413 above it). |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated CORS origins. `*` is rejected in production. |

Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(64))"`.

## Supabase PostgreSQL

Supabase offers (Project Settings -> Database -> Connection string):

- **Direct connection** (`db.<ref>.supabase.co:5432`): IPv6 only on the free tier. Best for migrations if your network supports it.
- **Session pooler** (port 5432 on the pooler host): IPv4 compatible, supports prepared statements.
- **Transaction pooler** (port 6543, pgbouncer transaction mode): best for serverless/many connections (e.g. Render). It does **not** support prepared statements.

Paste any of them as `DATABASE_URL` in the plain `postgresql://...` form; the app rewrites it to `postgresql+asyncpg://`.

pgbouncer caveat: the app creates asyncpg connections with `statement_cache_size=0` (and unique prepared-statement names), so the transaction pooler works. This is done in `app/core/database.py`.

SSL: asyncpg does not understand `?sslmode=...`. If present, it is stripped and translated (`require` -> TLS). Supabase requires TLS; append `?sslmode=require` if you hit SSL errors.

URL-encode special characters in the database password.

## Cloudflare R2

1. Cloudflare dashboard -> R2 -> create a bucket (`R2_BUCKET_NAME`).
2. R2 -> Manage API tokens -> create a token with *Object Read & Write* on that bucket. Copy the Access Key ID and Secret (`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`) and note your Account ID (`R2_ACCOUNT_ID`).
3. Public access: either connect a custom domain to the bucket (recommended for production) or enable the `r2.dev` URL (rate limited, for development). Set it as `R2_PUBLIC_URL` (no trailing slash).
4. CORS: images are loaded via plain `<img>` tags, so no bucket CORS is needed. Only add a CORS rule on the bucket if the frontend fetches the files via JavaScript/canvas. Uploads go through this API, never directly from the browser.

Uploaded files are stored as `hero/<uuid>.<ext>` where the extension matches the *real* detected format (jpg/png/webp). Images are stored as uploaded (no conversion). Original filenames are never used.

## Migrations

```bash
alembic upgrade head
```

`alembic/env.py` runs migrations through the async engine (asyncpg for Postgres, aiosqlite for SQLite), reading `DATABASE_URL` from settings. Quick local check without Postgres:

```bash
DATABASE_URL=sqlite+aiosqlite:///./local.db alembic upgrade head
```

Prefer the direct or session pooler URL for migrations.

## Create the initial admin

```bash
python -m scripts.create_admin                       # prompts for email and password
ADMIN_EMAIL=you@studio.com ADMIN_PASSWORD='...' python -m scripts.create_admin   # non-interactive
```

Passwords (min 12 chars) are hashed with argon2; nothing is stored in code.

## Run locally

```bash
uvicorn app.main:app --reload
```

Swagger UI: http://localhost:8000/docs (disabled when `ENVIRONMENT=production`). Health: `GET /health`.

## API summary

- `POST /api/v1/auth/login` `{email, password}` -> `{access_token, token_type}`
- `GET /api/v1/hero-images` (public, active only, sorted by `display_order`)
- Admin (header `Authorization: Bearer <token>`), prefix `/api/v1/admin/hero-images`:
  `GET ""`, `POST ""` (multipart: `image`, `title`, `subtitle`, `alt_text`, `display_order`, `is_active`),
  `PUT /{id}` (only supplied fields change), `PATCH /{id}/status`, `PATCH /reorder` (JSON array of `{id, display_order}`, all-or-nothing), `DELETE /{id}` (204).

Errors are always `{"detail": ...}`. If deleting from R2 fails the API returns 502 and keeps the DB row.

## Tests

```bash
pytest
```

Tests use in-memory SQLite and a mocked R2 storage service (no network or credentials needed).

## Deploy to Render

Docker (recommended): create a Web Service from this repo with *Runtime: Docker*. The Dockerfile starts `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`.

Native alternative:
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Settings:
- Health check path: `/health`
- Pre-deploy command: `alembic upgrade head`
- Environment variables: everything in the table above, with `ENVIRONMENT=production`, a strong `JWT_SECRET`, the Supabase `DATABASE_URL`, R2 values, and `ALLOWED_ORIGINS` set to your website/admin origins (no wildcard).
- Create the first admin once from the Render shell: `python -m scripts.create_admin`.
# chanthans-backend
