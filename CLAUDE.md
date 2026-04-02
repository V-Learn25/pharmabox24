# PharmaBox - Project Context for Claude

## What This Is
Pharmabox24 — a prescription collection analytics portal for pharmacies in Ireland. Pharmacies have automated parcel machines; this dashboard lets admins upload usage data (CSV/Excel) and gives each pharmacy a login to view their analytics.

## Current Status (2 April 2026)
- **App is LIVE on Railway** and login is working
- PostgreSQL database connected via Railway plugin (`DATABASE_URL` auto-injected)
- Admin account created and verified working (credentials from env vars)
- Email (Resend) NOT yet configured — `RESEND_API_KEY` not set, so password reset emails won't send yet
- No pharmacy data uploaded yet — database is empty (fresh PostgreSQL)
- Custom domain not yet configured — still on Railway's default URL
- Debug `/health` endpoint has been removed

## Stack
- **Backend:** Flask 3.0, SQLAlchemy, Flask-Login
- **Frontend:** Jinja2 templates, Tailwind CSS (CDN 3.4.17), Alpine.js 3.14.8, Chart.js 4.4.7
- **Database:** PostgreSQL on Railway (SQLite for local dev)
- **Email:** Resend API (env var: RESEND_API_KEY) — no SMTP
- **Hosting:** Railway (auto-deploys from GitHub on push to main)
- **WSGI:** Gunicorn (2 workers)

## GitHub
- Repo: https://github.com/V-Learn25/pharmabox24
- Branch: main (Railway deploys from this)

## Key Files
| File | Purpose |
|------|---------|
| `app.py` | All routes, forms, upload processing, email sending (~1160 lines) |
| `models.py` | SQLAlchemy models: User, Pharmacy, DailyStat, HourlyDistribution, Upload |
| `config.py` | Config from environment variables |
| `templates/base.html` | Layout with nav/sidebar (Tailwind + Alpine.js) |
| `templates/admin/` | Admin dashboard, upload, pharmacy/user CRUD |
| `templates/pharmacy/` | Pharmacy user dashboard with Chart.js |
| `templates/forgot_password.html` | Forgot password page |
| `templates/reset_password.html` | Token-based password reset page |
| `Procfile` | Gunicorn start command for Railway |
| `railway.json` | Railway build/deploy config |

## Architecture
- Monolithic Flask app (single `app.py`)
- Role-based access: `admin` sees everything, `pharmacy` sees own dashboard
- Data upload: admin uploads CSV/Excel → parsed → upserted into DailyStat/HourlyDistribution
- Charts: frontend fetches `/api/pharmacy/chart-data` (JSON) → Chart.js renders
- Password reset: token-based via email (Resend API), 1-hour expiry
- Admin credentials force-sync from `ADMIN_EMAIL`/`ADMIN_PASSWORD` env vars on every startup
- Email sending uses Resend HTTP API (stdlib `urllib`, no extra dependencies)

## Environment Variables (Railway)
| Variable | Required | Purpose |
|----------|----------|---------|
| `SECRET_KEY` | Yes | Flask session secret |
| `DATABASE_URL` | Yes (auto) | PostgreSQL — auto-set by Railway plugin, uses `${{Postgres.DATABASE_URL}}` reference |
| `ADMIN_EMAIL` | Yes | Admin login email (force-synced on every startup) |
| `ADMIN_PASSWORD` | Yes | Admin login password (force-synced on every startup) |
| `SITE_URL` | Yes | Base URL for email links (set to custom domain when ready) |
| `RESEND_API_KEY` | Optional | For password reset and notification emails (resend.com) |
| `MAIL_FROM` | Optional | Sender address for Resend (default: `Pharmabox24 <onboarding@resend.dev>`) |

## Development Workflow
1. Edit files in this directory
2. Test locally: `python app.py` (uses SQLite)
3. Commit and push: Railway auto-deploys
4. No CI/CD pipeline — just push to main

## What Was Done This Session (2 April 2026)
1. **Code audit and modernization** — fixed deprecated `datetime.utcnow`, `Query.get()`, bare `except:`, pinned CDN versions
2. **Production deployment setup** — created requirements.txt, Procfile, railway.json, .gitignore, runtime.txt
3. **Railway compatibility** — added `postgres://` → `postgresql://` URL fix, gunicorn, psycopg2-binary
4. **Security** — removed hardcoded admin password, admin credentials now sync from env vars
5. **Password reset flow** — forgot password page, token-based reset with 1-hour expiry
6. **Resend email integration** — replaced all SMTP code with Resend HTTP API
7. **Removed all pythonanywhere.com references**
8. **GitHub repo created** — https://github.com/V-Learn25/pharmabox24
9. **Deployed to Railway** — debugged DB connection and admin login issues, now working

## Next Steps (when development resumes)
- **Configure custom domain** in Railway and set `SITE_URL` env var
- **Set up Resend** — create account at resend.com, get API key, add to Railway env vars
- **Upload pharmacy data** — use the admin dashboard Upload Data page
- **Create pharmacy user accounts** — via admin Users page
- No database migrations yet (uses `db.create_all()`) — if schema changes needed, consider adding Flask-Migrate
- No test suite exists
- App is a single file (`app.py`) — may want to split into blueprints if it grows significantly
