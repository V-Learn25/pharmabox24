# PharmaBox - Project Context for Claude

## What This Is
Pharmabox24 — a prescription collection analytics portal for pharmacies in Ireland. Pharmacies have automated parcel machines; this dashboard lets admins upload usage data (CSV/Excel) and gives each pharmacy a login to view their analytics.

## Current Status (4 April 2026)
- **App is LIVE on Railway** — login working, data uploaded
- PostgreSQL database connected via Railway plugin (`DATABASE_URL` auto-injected)
- **Resend email configured** — `RESEND_API_KEY` set on Railway, password resets and upload notifications working
- Database has live data: 8+ pharmacies with weekly uploads (Weekly202608-202612.xlsx etc.)
- Custom domain not yet configured — still on Railway's default URL
- **Three-tier role system deployed**: super_admin, org_admin, pharmacy
- **Organisation tier added** — pharmacy chains can be grouped under an organisation
- **Admin user role manually set to `super_admin`** in the database (migration auto-migrates `admin` → `super_admin` on startup too)

## DEPLOYMENT CRITICAL
- **GitHub repo moved to:** https://github.com/V-Learn-Ltd/pharmabox24 (remote updated locally)
- **Dropbox conflict:** Dropbox renames `.txt` files to `.md` — `requirements.txt` and `runtime.txt` keep disappearing. Before every commit, run `git checkout -- requirements.txt runtime.txt` to restore them if deleted.
- **Migration runs on startup** via `migrate_db()` in `app.py` — uses raw SQL ALTER TABLE wrapped in try/except per operation. Safe to re-run (idempotent).
- `db.create_all()` creates new tables but CANNOT add columns to existing tables. That's why `migrate_db()` exists.

## Stack
- **Backend:** Flask 3.0, SQLAlchemy, Flask-Login
- **Frontend:** Jinja2 templates, Tailwind CSS (CDN 3.4.17), Alpine.js 3.14.8, Chart.js 4.4.7
- **Database:** PostgreSQL on Railway (SQLite for local dev)
- **Email:** Resend API (env var: RESEND_API_KEY) — no SMTP
- **Hosting:** Railway (auto-deploys from GitHub on push to main)
- **WSGI:** Gunicorn (2 workers)

## GitHub
- Repo: https://github.com/V-Learn-Ltd/pharmabox24
- Branch: main (Railway deploys from this)

## Key Files
| File | Purpose |
|------|---------|
| `app.py` | All routes, forms, upload processing, email sending, migration (~1520 lines) |
| `models.py` | SQLAlchemy models: Organisation, User, Pharmacy, DailyStat, HourlyDistribution, Upload |
| `config.py` | Config from environment variables (session=7 days, secure cookies, 16MB upload limit) |
| `templates/base.html` | Layout with role-based sidebar (super_admin / org_admin / pharmacy) |
| `templates/admin/` | Super admin: dashboard, upload, orgs CRUD, pharmacy CRUD, user CRUD, help page |
| `templates/org/` | Org admin: dashboard (bird's-eye), pharmacy view, user management |
| `templates/pharmacy/` | Pharmacy user dashboard with Chart.js |
| `Procfile` | Gunicorn start command for Railway |
| `railway.json` | Railway build/deploy config |

## Architecture
- Monolithic Flask app (single `app.py`)
- **Three-tier role system:**
  - `super_admin` — full system access (upload data, manage orgs/pharmacies/users). `is_super_admin()` also accepts legacy `admin` role for backwards compat.
  - `org_admin` — bird's-eye dashboard of their organisation's pharmacies, manage users within their org. Fully siloed — cannot see other organisations' data.
  - `pharmacy` — view own pharmacy dashboard only
- **Organisation model:** groups pharmacies under a parent company. Pharmacies have nullable `organisation_id` FK. Users have nullable `organisation_id` FK.
- Data upload: super admin uploads CSV/Excel → parsed → upserted into DailyStat/HourlyDistribution → file deleted after processing
- Charts: frontend fetches `/api/pharmacy/chart-data` (JSON) → Chart.js renders
- Password reset: token-based via email (Resend API), 1-hour expiry
- Admin credentials force-sync from `ADMIN_EMAIL`/`ADMIN_PASSWORD` env vars on every startup
- Email sending uses Resend HTTP API (stdlib `urllib`, no extra dependencies)

## Security Features (added 4 April 2026)
- Rate limiting on login (10 attempts/5 min per IP) and password reset
- Security headers: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy
- Secure session cookies: HTTPOnly, SameSite=Lax, Secure (when HTTPS)
- Open redirect protection on login `next` parameter
- CSRF token expiry handled gracefully (redirect + flash, not ugly error page)
- Session lifetime: 7 days
- Duplicate email/serial number validation on all create/edit forms
- Upload errors trigger db.session.rollback()
- Database indexes on frequently queried columns (pharmacy_id + date)

## Environment Variables (Railway)
| Variable | Required | Purpose |
|----------|----------|---------|
| `SECRET_KEY` | Yes | Flask session secret |
| `DATABASE_URL` | Yes (auto) | PostgreSQL — auto-set by Railway plugin |
| `ADMIN_EMAIL` | Yes | Super admin login email (force-synced on every startup) |
| `ADMIN_PASSWORD` | Yes | Super admin login password (force-synced on every startup) |
| `SITE_URL` | Yes | Base URL for email links (set to custom domain when ready) |
| `RESEND_API_KEY` | Yes | For password reset and notification emails (resend.com) |
| `MAIL_FROM` | Optional | Sender address for Resend (default: `Pharmabox24 <onboarding@resend.dev>`) |

## Development Workflow
1. Edit files in this directory
2. Test locally: `python app.py` (uses SQLite)
3. **Before committing:** `git checkout -- requirements.txt runtime.txt` (Dropbox deletes them)
4. Commit and push to main: Railway auto-deploys
5. No CI/CD pipeline — just push to main

## Session History

### Session 1 (2 April 2026)
1. Code audit and modernization — fixed deprecated APIs, pinned CDN versions
2. Production deployment setup — requirements.txt, Procfile, railway.json, .gitignore, runtime.txt
3. Railway compatibility — postgres:// URL fix, gunicorn, psycopg2-binary
4. Security — removed hardcoded admin password, env var sync
5. Password reset flow — forgot password page, token-based reset
6. Resend email integration — replaced SMTP with Resend HTTP API
7. GitHub repo created, deployed to Railway

### Session 2 (4 April 2026)
1. **Admin help page** — `/admin/help` with plain-language user guide (admin-only)
2. **Security hardening** — rate limiting, security headers, secure cookies, open redirect protection, XSS fixes, duplicate validation, upload rollback/cleanup, DB indexes
3. **CSRF token handling** — expired tokens now redirect gracefully instead of "Bad Request" error
4. **Session lifetime** extended from 8 hours to 7 days
5. **Organisation tier** — new Organisation model, three-tier role system (super_admin/org_admin/pharmacy)
6. **Org admin features** — bird's-eye dashboard, pharmacy drill-down, user management within org
7. **Super admin features** — organisation CRUD, pharmacy/user assignment to orgs
8. **Database migration** — `migrate_db()` adds new columns via ALTER TABLE (idempotent, defensive)
9. **GitHub remote updated** to V-Learn-Ltd/pharmabox24
10. **Resend configured** on Railway — emails now working

### Deployment issue (4 April 2026)
- Multiple deployments failed — root cause was `@csrf.error_handler` which doesn't exist in Flask-WTF 1.2.1
- Fixed to `@app.errorhandler(CSRFError)` — the correct API
- Also fixed: migration order (raw SQL now runs BEFORE `db.create_all()` so ORM columns match DB)
- User manually set role to `super_admin` in database via Railway's DB console

## Database Backups
- **Script:** `backup.py` in project root
- **Method:** `pg_dump` → gzip, stored on Railway volume at `/data/backups/`
- **Retention:** Keeps 7 most recent backups, deletes oldest automatically
- **Alerts:** Optional email notification on failure via Resend (set `BACKUP_NOTIFY_EMAIL` env var)

### Railway Setup for Backup Service
1. In your Railway project, click **New** → **Service** → select the same GitHub repo
2. In the new service's **Settings:**
   - **Start Command:** `python backup.py`
   - **Cron Schedule:** `0 2 * * *` (runs daily at 2:00 AM UTC)
   - **Root Directory:** leave blank (same repo root)
3. Add a **Volume** to the backup service:
   - Mount path: `/data`
4. Add **Environment Variables** (or share from the web service):
   - `DATABASE_URL` — reference the PostgreSQL plugin: `${{Postgres.DATABASE_URL}}`
   - `RESEND_API_KEY` — same as web service (optional, for failure alerts)
   - `BACKUP_NOTIFY_EMAIL` — email address to alert on failure (optional)
   - `MAIL_FROM` — same as web service (optional)
5. Deploy — Railway will run the backup daily and you'll see output in the service logs

### Restoring from Backup
1. Download the backup from the Railway volume (via the service's shell)
2. Decompress: `gunzip pharmabox24_YYYYMMDD_HHMMSS.sql.gz`
3. Restore: `psql $DATABASE_URL < pharmabox24_YYYYMMDD_HHMMSS.sql`

## Next Steps (when development resumes)
- **Verify deployment succeeded** — user should see Organisations in sidebar after deploy
- **Set up backup cron service** on Railway (see instructions above)
- **Configure custom domain** in Railway and set `SITE_URL` env var
- **Create first organisation** — test the full flow: create org → assign pharmacies → create org admin → verify org admin dashboard
- **Test org admin isolation** — confirm org admin cannot see other orgs' pharmacies
- No test suite exists — consider adding basic route tests
- App is a single file (`app.py` ~1520 lines) — may want to split into blueprints if it grows
- Consider adding Flask-Migrate for future schema changes instead of manual ALTER TABLE
