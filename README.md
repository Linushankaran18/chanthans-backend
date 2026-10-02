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
| `APP_TIMEZONE` | `America/Toronto` | IANA timezone of the studio (validated at startup). Used for "today", booking numbers (year) and conflict/Google event times. |
| `FRONTEND_URL` | `http://localhost:5173` | Admin dashboard origin; users return to `FRONTEND_URL/dashboard/integrations` after Google sign-in. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | | OAuth client credentials (see below). Blank = integration disabled. |
| `GOOGLE_REDIRECT_URI` | | Must exactly match an authorized redirect URI of the OAuth client. |
| `GOOGLE_CALENDAR_SCOPES` | `openid email https://www.googleapis.com/auth/calendar.events` | Space-separated OAuth scopes. |
| `GOOGLE_CALENDAR_DEFAULT_ID` | `primary` | Calendar that receives booking events. |
| `GOOGLE_LOGIN_REDIRECT_URI` | | Redirect URI for "Continue with Google" dashboard sign-in, e.g. `https://api.chanthans.com/api/v1/auth/google/callback`. |
| `GOOGLE_LOGIN_ALLOWED_EMAILS` | | Comma-separated Google emails allowed to sign in (case-insensitive). Empty = Google sign-in disabled. |
| `TOKEN_ENCRYPTION_KEY` | | Fernet key encrypting Google tokens at rest. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

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

- `GET /api/v1/auth/me` -> `{id, email, full_name, role}`
- Bookings (admin), prefix `/api/v1/admin/bookings`: `GET ""` (filters `status` (repeatable / comma-separated), `search`, `date_from`, `date_to`, `service_type`, `sort` = `date_desc|date_asc|created_desc`, `page`, `page_size` <= 100), `GET /service-types`, `POST ""`, `GET /{id}`, `PUT /{id}`, `PATCH /{id}/status`, `POST /{id}/calendar-sync`, `DELETE /{id}` (204).
  Overlapping CONFIRMED bookings on the same day return `409 {"detail", "code": "booking_conflict", "conflicts": [...]}`. Invalid status transitions return 400.
- `GET /api/v1/admin/dashboard/summary`
- Invoices (admin), prefix `/api/v1/admin/invoices`: `GET ""` (filters `status` (repeatable / comma-separated, `OVERDUE` allowed), `overdue`, `search` (invoice #, customer, booking #), `date_from`, `date_to` (issue date), `page`, `page_size` <= 100), `GET /settings` (business defaults), `GET /prefill?booking_id=` (unsaved draft built from a booking), `GET /by-booking/{booking_id}` (invoice or `null`), `POST ""`, `GET /{id}`, `PUT /{id}`, `PATCH /{id}/status` (`ISSUED` | `CANCELLED`), `PATCH /{id}/payment` (`{amount_paid}` or `{mark_as_paid: true}`), `DELETE /{id}` (204, drafts and cancelled only), `GET /{id}/pdf?disposition=attachment|inline`.
- Google sign-in (no bearer): `GET /api/v1/auth/google/enabled` -> `{enabled}`, `GET /api/v1/auth/google/login` (302 to Google), `GET /api/v1/auth/google/callback` (302 to `FRONTEND_URL/auth/google/complete#token=<jwt>` or `FRONTEND_URL/login?google_error=<code>`).
- Google Calendar: `GET /api/v1/admin/integrations/google-calendar/{status,connect}`, `POST .../disconnect`, and the public OAuth `GET /api/v1/integrations/google-calendar/callback`.

Set the admin's display name with `ADMIN_FULL_NAME=... python -m scripts.create_admin --reset` (optional).

Errors are always `{"detail": ...}`. If deleting from R2 fails the API returns 502 and keeps the DB row.

## Google sign-in for the dashboard

"Continue with Google" lets allow-listed Google accounts sign in to the admin dashboard (password login keeps working). It reuses the same OAuth client as the calendar integration but requests only `openid email`. Accounts must have a verified email and appear in `GOOGLE_LOGIN_ALLOWED_EMAILS`; a missing admin user is created on first sign-in with an unusable random password.

Setup:
1. In Google Cloud -> APIs & Services -> Credentials, open the SAME OAuth client and add these Authorized redirect URIs:
   - `http://localhost:8000/api/v1/auth/google/callback` (local)
   - `https://api.chanthans.com/api/v1/auth/google/callback` (production)
2. On Render set `GOOGLE_LOGIN_REDIRECT_URI` (the matching URI) and `GOOGLE_LOGIN_ALLOWED_EMAILS` (comma-separated emails); `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `FRONTEND_URL` are shared with the calendar integration.

## Google Calendar integration

Confirmed bookings are mirrored to the studio's Google Calendar (created on confirm, updated on edit, removed on cancel/delete). Google problems never block a booking: the booking just shows `calendar_sync_status=FAILED` and can be retried. Without the Google variables the app runs normally and the integration reports `configured: false`.

Google Cloud setup (one time):
1. <https://console.cloud.google.com> -> create or pick a project.
2. APIs & Services -> Library -> enable **Google Calendar API**.
3. APIs & Services -> OAuth consent screen: choose External, fill the app name and support email, add the scopes `openid`, `email` and `.../auth/calendar.events`. While the app is in *Testing*, add the studio owner's Google account under **Test users** (only test users can connect; tokens of testing apps expire after 7 days, so click "Publish app" for production use).
4. APIs & Services -> Credentials -> Create credentials -> OAuth client ID -> **Web application**. Add these **Authorized redirect URIs**:
   - `http://localhost:8000/api/v1/integrations/google-calendar/callback` (local)
   - `https://api.chanthans.com/api/v1/integrations/google-calendar/callback` (production)
5. Copy the client ID and secret into `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, set `GOOGLE_REDIRECT_URI` to the matching URI above, generate `TOKEN_ENCRYPTION_KEY` (command in the table) and set `FRONTEND_URL`.
6. Open the dashboard -> Integrations -> Connect Google Calendar.

Render: set `APP_TIMEZONE`, `FRONTEND_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `TOKEN_ENCRYPTION_KEY` (and optionally `GOOGLE_CALENDAR_SCOPES`, `GOOGLE_CALENDAR_DEFAULT_ID`). Run `alembic upgrade head` (the pre-deploy command does) to create the `bookings` and `google_calendar_integrations` tables. New Python dependencies: `httpx`, `cryptography`, `tzdata` (all in `requirements.txt`).

## Invoices

One booking has one active invoice (`INV-YYYY-NNNN`, numbered by the backend; the table allows more per booking later). The backend recalculates everything from the line items: `line = qty x unit price`, `subtotal`, `- discount`, `+ tax` (`tax_rate` is a percentage, e.g. `13`), `total`, `- amount paid = balance due`. All money is `Decimal`, rounded half-up to cents. Overpayment and a discount above the subtotal are rejected.

Status is stored as `DRAFT | ISSUED | PARTIALLY_PAID | PAID | CANCELLED` and follows `amount_paid` once issued. `OVERDUE` is derived (past `due_date` with a balance) and exposed as `display_status` / `is_overdue`; it is never written to the database. A booking with an invoice cannot be deleted until the invoice is deleted (drafts and cancelled invoices only; issued ones must be cancelled first).

Business identity, currency, default tax, notes and terms come from the `BUSINESS_*` / `INVOICE_*` variables (see `.env.example`); tax is never assumed. `GET /admin/invoices/settings` hands the non-secret subset to the dashboard.

PDFs are rendered on request (Jinja2 template `app/templates/invoice.html` -> WeasyPrint), never stored. Fonts (Inter, Cormorant Garamond, OFL) are bundled in `app/assets/fonts`. The logo is `BUSINESS_LOGO_URL`, fetched once an hour and embedded as a data URI; if it is unset or unreachable the bundled `app/assets/logo.png` is used, and without any logo the business name is shown. While rendering, only data URIs and the bundled fonts can be loaded, and user text is HTML-escaped.

WeasyPrint needs the Pango system libraries: the Dockerfile installs them; locally run `brew install pango` (macOS) or `apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0` (Debian/Ubuntu). Without them the PDF endpoint answers 500 "Unable to generate the PDF" and the PDF tests are skipped. Run `alembic upgrade head` to create `invoices` and `invoice_items` (migration `0003`).

## Tests

```bash
pytest
```

Tests use in-memory SQLite, a mocked R2 storage service and a fake Google Calendar client (no network or credentials needed).

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
