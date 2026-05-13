# PharmaBox — Session Handoff

Last updated: **2026-05-13**
Maintained by: Neil + Claude
Companion docs: [CLAUDE.md](CLAUDE.md) (architecture, deployment), [README.md](README.md)

---

## 0. Pick up cold — start here

If you're returning to this project after a break, do this first:

1. **Check Railway** — is the live service green? Visit `https://managementinfo.pharmabox24.co.uk/login` — should render cleanly (form + green sign-in button, NOT a giant green logo).
2. **Read § 2 (Current State)** below. There are two known open items: a dual-super_admin situation and a Resend email 403.
3. **Run `/admin/email-health`** as super_admin if email is still failing — it diagnoses Resend in 30 seconds.
4. **Look at git log** for recent commits — they explain the most recent decisions:
   ```bash
   git log --oneline -10
   ```

---

## 1. What this is

Pharmabox24 — prescription collection analytics portal for Irish pharmacies. Admins upload CSV/Excel parcel data, pharmacies see their own dashboard with charts. Three-tier role system (super_admin / org_admin / pharmacy). Flask 3.0 + Postgres on Railway. See [CLAUDE.md](CLAUDE.md) for the full architecture.

**Live URL:** `https://managementinfo.pharmabox24.co.uk` (custom domain on Railway)
**GitHub:** https://github.com/V-Learn-Ltd/pharmabox24
**Stack:** Flask, SQLAlchemy, Flask-Login, Tailwind CDN, Alpine.js, Chart.js, Resend (email), Railway (host + Postgres)

---

## 2. Current production state (as of 2026-05-13)

### Health
- ✅ Deployed and serving
- ✅ HTTPS via Railway managed cert
- ✅ Postgres migrations applied (organisation_id, session_version columns present)
- ⚠️ **Two super_admin rows** may now exist (see § 2.1 below)
- ❌ **Resend email returning 403** on at least one external recipient (`mark.piper1@nhs.net`). Investigate via `/admin/email-health`.

### 2.1 Dual super_admin situation
The `Security hardening` commit's admin-sync logic used to try to overwrite the existing super_admin's email with `ADMIN_EMAIL` env var. That collided with another user owning `info@pharmabox24.co.uk` → UniqueViolation → sync aborted. The follow-up fix (`Fix three issues...` commit) changed the logic to:
- **If a user with `ADMIN_EMAIL` exists → promote them to super_admin** (sync password too)
- **Else if any super_admin exists → sync the password under their existing email** (no rename)
- **Else create a fresh super_admin**

The first branch fires on the live DB. So you now have two super_admins:
- The original super_admin (whatever email it had — check `/admin/users`)
- `info@pharmabox24.co.uk` (newly promoted)

**Action needed:** decide which one is canonical. Either:
- (a) Demote the non-canonical one to `pharmacy`/`org_admin` via `/admin/users`, OR
- (b) Change Railway's `ADMIN_EMAIL` env var to match the existing super_admin, restart, then demote/delete the `info@…` super_admin row.

### 2.2 Email pipeline — resolved 2026-05-13

**Resolution timeline:**
- ✅ `pharmabox24.co.uk` verified on Resend (Ireland, eu-west-1) since ~April 2026.
- ✅ `RESEND_API_KEY` rotated 2026-05-13 (previous key leaked in a screenshot during diagnosis).
- ✅ `MAIL_FROM` updated to use the verified `pharmabox24.co.uk` domain.
- ✅ `SITE_URL` updated to `https://managementinfo.pharmabox24.co.uk`.
- ✅ Dead Gmail SMTP env vars removed (`MAIL_SERVER`, `MAIL_PORT`, `MAIL_DEFAULT_SENDER` — leftover from the pre-April Resend migration; code never read them).

**Final verification step:**
Hit `/admin/email-health` → "Send test email" → should now land in inbox. If it doesn't, check Railway logs for `Resend HTTPError` lines (the new `send_email` captures the full response body, so the error message will be specific).

**NHSmail nuance:** NHSmail (the recipient at `mark.piper1@nhs.net`) is strict about inbound. SPF + DKIM are already in place via Resend's DNS records. Add a `p=none` DMARC record at `_dmarc.pharmabox24.co.uk` to get failure reports without bouncing. If the first NHS-bound send lands in junk, ask Mark to "Not junk" it once — NHSmail learns per-user.

**Architecture note:** the app uses **Resend HTTP API only** (`urlopen` to `api.resend.com/emails` in `send_email()`). There is no SMTP path, no `smtplib`, no Flask-Mail. The Gmail SMTP env vars that lived on Railway until 2026-05-13 were inert leftover config from before the April 2 migration to Resend, not active mail routing.

---

## 3. Recent commits (audit + hardening sweep)

```
abc42b3  Batch dashboard queries — fix N+1 before scale bites
bf0ed0e  Fix Cloudflare 1010 on Resend + force Alpine.js cache busting
c09e198  HANDOFF.md: email pipeline resolved
4ff0cce  HANDOFF.md: simplify email fix — pharmabox24.co.uk already verified
aaaa38a  Add HANDOFF.md — pick-up-cold session document
122b4f3  Remove SRI from all CDN scripts — hashes mismatch in browsers
a06ee6b  Drop SRI hash from cdn.tailwindcss.com — it's a runtime JIT compiler
76547a4  Fix three issues surfaced in the previous deploy
9076a71  Deploy hotfix + audit follow-ups
0f99517  Security hardening — pre-prod audit findings
3e1be7a  (pre-audit) Fix typo: pharmacyies → pharmacies in organisations list
```

### 3.1 What was fixed (from the pre-production audit, 24 items)

**CRITICAL (6):**
- SECRET_KEY refuses to boot without a 32+ char key in prod
- `changeme123` default-admin fallback removed
- Open redirect via `//evil.com` patched (urlparse-based safe-next)
- Rate limiter: thread-safe, bounded memory, ProxyFix wired for real client IPs
- Backup script: `shell=True` removed (replaced with libpq env vars + Python gzip pipe)
- `init_db` now log-and-continues instead of silently failing; SQLite migration parity added

**HIGH (10):**
- Email HTML injection patched (every user-controlled value escaped via `markupsafe.escape`)
- Excel zip-bomb defence (`read_only=True`, `UPLOAD_MAX_ROWS=100k` cap)
- Reset tokens now use `itsdangerous` HMAC + `session_version` binding
- `int()` crash on bad cells fixed — per-row safe int with skipped count
- Upload filename UUID-prefix + magic-byte verification
- `SESSION_COOKIE_SECURE` hard-defaults True in non-dev
- `/logout` is POST-only with CSRF form
- CSP, HSTS (HTTPS-only), Permissions-Policy added; deprecated X-XSS-Protection dropped
- SRI hashes on Alpine, Chart.js, Font Awesome (NOT Tailwind — see § 5)
- send_email: narrow exceptions, captures HTTP status + body

**MEDIUM (8):**
- Password min length 6 → 12
- Last-admin deletion refused
- Hourly chart query: `SELECT MAX(month)` before fetch (was: scan all rows)
- FK indexes on `users.pharmacy_id`, `users.organisation_id`, `pharmacies.organisation_id`
- `session_version` column for force-logout / token invalidation
- Email normalisation (strip+lower) across all CRUD
- `_today()` uses configured `DISPLAY_TIMEZONE` (Europe/Dublin)
- Excel `report_month` strict — raises if no parseable date instead of defaulting to today

**Other:**
- Opt-in "Remember me" checkbox (default: browser-session-only)
- `/admin/email-health` diagnostic page (super_admin only)
- Password-change notification email
- `debug=True` gated on `FLASK_DEBUG=1`
- Pharmacy-name sanitiser strips `<>$\``, control chars

### 3.2 Post-audit follow-ups (landed 2026-05-13)

- **N+1 dashboard queries fixed (#12).** `get_pharmacy_stats_batch()` does 4 grouped aggregate queries regardless of pharmacy count. Replaces 5-queries-per-pharmacy loops in `admin_dashboard` and `org_dashboard`. Benchmark at 30 pharmacies × 60 days: 47.9ms → 3.3ms (14.7× speedup) on local SQLite; bigger multiple on Railway Postgres. **The "will bite at 20+ pharmacies" risk is now closed.**
- **Cloudflare error 1010 on Resend** — Resend's API sits behind Cloudflare WAF, which now rejects requests with the default `Python-urllib/X` user-agent. Both `send_email()` POST and `_resend_probe()` GET now set a descriptive User-Agent.
- **All CDN SRI hashes removed.** The hashes I computed via `curl` didn't match what browsers actually receive (jsdelivr does content-negotiation/encoding-aware compression that changes the served bytes). `crossorigin="anonymous"` also dropped to force a fresh fetch past jsdelivr's `immutable` cache headers (which can pin a failed-SRI state in browsers even through hard-refresh). Proper fix is self-host (§ 4.2 #15).
- **Two-super_admin situation surfaced.** First production admin sync collided with an existing `info@pharmabox24.co.uk` user; the rewritten admin-sync logic now promotes that row to super_admin instead of trying to overwrite emails. Result is two super_admin rows by design until you reconcile (§ 2.1).

---

## 4. Outstanding / deferred work

From the original audit, ranked by ROI:

### 4.1 Quick wins worth doing soon (each <2 hours)
- [x] ~~Verify the Tailwind SRI mismatch was the only CDN issue~~ — it wasn't. All CDN SRI hashes were broken, all dropped 2026-05-13. § 5.2 has the detail.
- [ ] **Resolve the dual super_admin situation** (§ 2.1 above) — pick one canonical super_admin via `/admin/users`.
- [x] ~~Diagnose Resend 403 via `/admin/email-health`~~ — done 2026-05-13. Final action: verify the test-email send from `/admin/email-health` once Railway picks up the User-Agent fix + corrected `MAIL_FROM` env var format.
- [ ] **#25 — Remove legacy `'admin'` role compat** in `models.py` once you've verified the live DB has zero `role='admin'` rows. Search `models.py` for the comment "Legacy 'admin' role kept as a safety net".

### 4.2 Worth doing before scaling beyond ~20 pharmacies
- [x] ~~**#12 — N+1 queries on admin/org dashboards.**~~ Fixed 2026-05-13. `get_pharmacy_stats_batch()` does 4 grouped queries regardless of pharmacy count. **Dashboards now scale linearly with pharmacy count without per-pharmacy query overhead — the "will bite at 20+" risk is closed.** Detail-page views (`pharmacy_dashboard`, `admin_pharmacy_view`, `org_pharmacy_view`) still use the per-pharmacy `get_pharmacy_stats()` because they need `recent_records`, which doesn't make sense to batch.
- [ ] **#18 — Funnel all pharmacy access through `_can_access_pharmacy()`.** Currently safe but fragile. Defense-in-depth refactor, not performance.
- [ ] **#15 — Self-host Tailwind + Alpine + Chart.js.** Compile via npm/PostCSS at build time; serve static CSS/JS from `/static/`. Replaces all four runtime CDN dependencies, allows real SRI, removes the entire supply-chain attack surface.

### 4.3 Worth doing before specific milestones
- [ ] **#36 — Prompt-injection hardening.** Required before any LLM integration. Pharmacy names from uploads are an untrusted-input vector.
- [ ] **#24 — Blueprint split.** `app.py` is ~2,000 lines. Threshold for splitting is ~2,500–3,000 lines or 2+ contributors.
- [ ] **#31 — Soft-delete UX.** Build the first time a customer asks "can you undo that delete".

### 4.4 Low priority
- [ ] **#19 — Timing-equal forgot-password.** Needs background queue (Redis + RQ/Celery).
- [ ] **#28 — Stricter pharmacy name regex.** Sanitiser already covers the dangerous bits.
- [ ] **#33 — Logger unification.** Pure cosmetic.
- [ ] **#34 — CSV header strictness.** Already raises if `S/N` missing.
- [ ] **#38 — org_admin redirect-loop.** Currently handled by stub render; fix during blueprint split.
- [ ] **#40 — Resend retry/idempotency.** Defer until a transient-failure incident demands it.

---

## 5. Known gotchas / decisions

### 5.1 Dropbox renames `.txt` to `.md`
**This is no longer the dev environment problem it was** — the repo now lives outside Dropbox (see § 6). The old Dropbox location may still hold a stale copy; do not develop against it.

If you ever clone fresh and find `requirements.txt`/`runtime.txt` missing but `requirements.md`/`runtime.md` present: that's Dropbox interference. Run `git checkout HEAD -- requirements.txt runtime.txt` to restore them.

### 5.2 Tailwind CDN ≠ static CDN
`cdn.tailwindcss.com` is a **runtime JIT compiler** that serves different bytes per browser. Do NOT add SRI to it (it'll always fail). Either tolerate the supply-chain risk or self-host (#15 in § 4.2). SRI is in place for the other three CDN scripts.

### 5.3 Init_db is log-and-continue
The audit recommendation was "fail loudly on init errors." We tried that and a UniqueViolation on admin-sync crashed every worker on deploy. Current behaviour: log full traceback to Railway, keep workers serving. Trade-off: bad migration could silently leave the app running against a half-migrated schema. The migration steps are all idempotent (`IF NOT EXISTS`) so this is acceptable.

### 5.4 Two super_admins is currently expected state
Per § 2.1. Resolve manually via `/admin/users`.

### 5.5 Pharmacy/SQLite local dev
`pharmacy.db` is gitignored. On first local run, `init_db()` creates the file and runs the SQLite migration branch (different code path from the Postgres branch). No admin will exist on a fresh local DB — set `ADMIN_PASSWORD=…` and `PHARMABOX_ENV=development` to bootstrap one, or set `PHARMABOX_ENV=development` to allow boot without an admin.

### 5.6 Backup service
Separate Railway service running `backup/backup.py` on a cron (`0 2 * * *` UTC). Stores `.sql.gz` files on a `/data` volume, retains 7 days. Notifies via Resend if configured (`BACKUP_NOTIFY_EMAIL`). The script no longer uses `shell=True`; pg_dump is called via `subprocess.Popen` with libpq env vars piped to `gzip.open()` in Python.

---

## 6. Repo location & dev workflow

**As of 2026-05-13, the repo lives at `~/Projects/pharmabox24/` — NOT in Dropbox.**

If you're reading this from the Dropbox location at:
```
/Users/neilmk/V-Learn Dropbox/.../PharmaBox/pharmacy-dashboard/
```
then you're in the stale copy. The Dropbox copy may still receive sync events but should not be developed against. Once you've verified the new location works, the Dropbox folder can be deleted (or kept as a passive backup — Dropbox's history is a nice-to-have, but git is authoritative).

### Local dev
```bash
cd ~/Projects/pharmabox24
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
export PHARMABOX_ENV=development
export ADMIN_PASSWORD="something-at-least-12-chars"
export ADMIN_EMAIL="you@example.com"
python app.py
# → http://localhost:5000
```

### Deploy
1. Edit code in `~/Projects/pharmabox24/`
2. `git commit -am "..."` and `git push`
3. Railway auto-deploys from `main`
4. Watch deploy logs in Railway UI; check the runtime logs for `init_db` status and any startup errors

---

## 7. Required env vars on Railway

| Variable | Required | Notes |
|----------|----------|-------|
| `SECRET_KEY` | YES | 32+ chars or app refuses to boot in prod |
| `DATABASE_URL` | YES (auto) | Set by Railway Postgres plugin |
| `ADMIN_EMAIL` | YES | Used to identify the super_admin to sync |
| `ADMIN_PASSWORD` | YES | Must be 12+ chars or sync skips |
| `SITE_URL` | YES | Used to build email links (e.g. `https://managementinfo.pharmabox24.co.uk`) |
| `RESEND_API_KEY` | YES | For password reset + upload notifications |
| `MAIL_FROM` | YES (effectively) | Should match a verified Resend domain. If missing, defaults to sandbox `onboarding@resend.dev` |
| `DISPLAY_TIMEZONE` | NO | Defaults to `Europe/Dublin` |
| `TRUSTED_PROXY_COUNT` | NO | Defaults to 1 (Railway depth) |
| `FLASK_ENV` / `PHARMABOX_ENV` | NO | Only set to `development` for local dev |
| `BACKUP_NOTIFY_EMAIL` | NO | Optional alert recipient for backup service failures |

---

## 8. When you come back

A reasonable next-session plan:

1. **Verify production is green** (load `/login`, click around).
2. **Diagnose the email 403** via `/admin/email-health`. If domain unverified on Resend, add DNS records and update `MAIL_FROM`.
3. **Resolve dual super_admin** via `/admin/users`.
4. **Pick one quick win** from § 4.1:
   - Self-hosted Tailwind (#15) is the most impactful — closes the last supply-chain hole and lets you add real SRI.
   - Removing legacy admin role compat (#25) is the smallest — just confirm DB state and delete 3 lines in `models.py`.
5. **Update this document** with whatever new state emerges.

---

## 9. People & context

- **Neil Maxwell-Keys** — founder, primary developer
- **Hannah Fennelly** — onboarding (separate role, not on this project AFAIK)
- **Zohair** — Meta ads manager (NOT on this project)

The PharmaBox project is part of V-Learn Ltd's portfolio. It's NOT the core V-Learn course business. See `~/V-Learn Dropbox/Neil Maxwell-Keys/Documents/NeilOS/01 CEO/` for the broader CEO context.
