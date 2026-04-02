# Pharmabox24 - Prescription Collection Analytics Portal

Dashboard for pharmacy prescription collection machine analytics. Admins upload data (CSV/Excel), pharmacies view their analytics via dashboards with charts.

## Stack

- **Backend:** Flask 3.0, SQLAlchemy, Flask-Login
- **Frontend:** Tailwind CSS, Alpine.js, Chart.js
- **Database:** PostgreSQL (production) / SQLite (development)

## Local Development

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your values
python app.py
```

Default admin: see ADMIN_EMAIL/ADMIN_PASSWORD in .env

## Deploy to Railway

1. Push to GitHub
2. Connect repo in Railway dashboard
3. Add a PostgreSQL plugin
4. Set environment variables (see .env.example)
5. Deploy — Railway auto-detects Python and uses the Procfile

### Required Railway Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Random secret for sessions |
| `DATABASE_URL` | Auto-set by Railway PostgreSQL plugin |
| `ADMIN_EMAIL` | Initial admin login email |
| `ADMIN_PASSWORD` | Initial admin login password |
| `SITE_URL` | Your Railway app URL (for email links) |

## Features

- Role-based access (Admin / Pharmacy User)
- CSV and Excel data upload with auto-parsing
- Interactive dashboards with Chart.js
- Email notifications on data uploads
- Pharmacy and user management
