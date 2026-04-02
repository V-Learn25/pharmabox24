# PharmaBox - Project Context for Claude

## What This Is
Pharmabox24 — a prescription collection analytics portal for pharmacies in Ireland. Pharmacies have automated parcel machines; this dashboard lets admins upload usage data (CSV/Excel) and gives each pharmacy a login to view their analytics.

## Stack
- **Backend:** Flask 3.0, SQLAlchemy, Flask-Login
- **Frontend:** Jinja2 templates, Tailwind CSS (CDN), Alpine.js, Chart.js
- **Database:** PostgreSQL on Railway (SQLite for local dev)
- **Email:** Resend API (env var: RESEND_API_KEY)
- **Hosting:** Railway (auto-deploys from GitHub on push to main)

## GitHub
- Repo: https://github.com/V-Learn25/pharmabox24
- Branch: main (Railway deploys from this)

## Key Files
| File | Purpose |
|------|---------|
| `app.py` | All routes, forms, upload processing, email sending |
| `models.py` | SQLAlchemy models: User, Pharmacy, DailyStat, HourlyDistribution, Upload |
| `config.py` | Config from environment variables |
| `templates/base.html` | Layout with nav/sidebar (Tailwind + Alpine.js) |
| `templates/admin/` | Admin dashboard, upload, pharmacy/user CRUD |
| `templates/pharmacy/` | Pharmacy user dashboard with Chart.js |

## Architecture
- Monolithic Flask app (single `app.py` — ~1150 lines)
- Role-based access: `admin` sees everything, `pharmacy` sees own dashboard
- Data upload: admin uploads CSV/Excel → parsed → upserted into DailyStat/HourlyDistribution
- Charts: frontend fetches `/api/pharmacy/chart-data` (JSON) → Chart.js renders
- Password reset: token-based via email (Resend API), 1-hour expiry
- Admin credentials sync from `ADMIN_EMAIL`/`ADMIN_PASSWORD` env vars on every startup

## Environment Variables (Railway)
| Variable | Required | Purpose |
|----------|----------|---------|
| `SECRET_KEY` | Yes | Flask session secret |
| `DATABASE_URL` | Yes (auto) | PostgreSQL (auto-set by Railway plugin) |
| `ADMIN_EMAIL` | Yes | Admin login email (synced on startup) |
| `ADMIN_PASSWORD` | Yes | Admin login password (synced on startup) |
| `SITE_URL` | Yes | Base URL for email links |
| `RESEND_API_KEY` | Optional | For password reset and notification emails |
| `MAIL_FROM` | Optional | Sender address for Resend |

## Development Workflow
1. Edit files in this directory
2. Test locally: `python app.py` (uses SQLite)
3. Commit and push: Railway auto-deploys
4. No CI/CD pipeline — just push to main

## Known Limitations / Future Work
- No database migrations (uses `db.create_all()` — schema changes need manual handling)
- No test suite
- Upload processing is synchronous (large files could timeout)
- No rate limiting on API endpoints
- `/health` endpoint exists for debugging — remove when no longer needed
- App is a single file (`app.py`) — may want to split into blueprints if it grows significantly
