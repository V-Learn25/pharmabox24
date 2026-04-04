"""
Daily PostgreSQL backup with 7-day retention.

Run as a Railway cron service: python backup.py
Stores backups in /data/backups/ (Railway volume mount).
Keeps the 7 most recent backups, deletes the rest.
"""

import os
import sys
import subprocess
import glob
from datetime import datetime

BACKUP_DIR = os.environ.get('BACKUP_DIR', '/data/backups')
DATABASE_URL = os.environ.get('DATABASE_URL', '')
KEEP_BACKUPS = int(os.environ.get('KEEP_BACKUPS', '7'))

# Optional: send email notification on failure
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
NOTIFY_EMAIL = os.environ.get('BACKUP_NOTIFY_EMAIL', '')


def send_alert(subject, message):
    """Send email alert via Resend if configured."""
    if not RESEND_API_KEY or not NOTIFY_EMAIL:
        return
    try:
        import json
        from urllib.request import Request, urlopen
        payload = json.dumps({
            'from': os.environ.get('MAIL_FROM', 'Pharmabox24 <onboarding@resend.dev>'),
            'to': [NOTIFY_EMAIL],
            'subject': f'Pharmabox24 Backup: {subject}',
            'html': f'<p>{message}</p>'
        }).encode()
        req = Request(
            'https://api.resend.com/emails',
            data=payload,
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            },
            method='POST'
        )
        urlopen(req, timeout=10)
    except Exception as e:
        print(f'Failed to send alert email: {e}')


def run_backup():
    if not DATABASE_URL:
        print('ERROR: DATABASE_URL not set')
        send_alert('FAILED', 'DATABASE_URL environment variable is not set.')
        sys.exit(1)

    # Fix Railway's postgres:// URL
    db_url = DATABASE_URL
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)

    # Create backup directory
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Generate filename with timestamp
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = f'pharmabox24_{timestamp}.sql.gz'
    filepath = os.path.join(BACKUP_DIR, filename)

    print(f'Starting backup: {filename}')
    print(f'Backup directory: {BACKUP_DIR}')

    # Run pg_dump piped through gzip
    try:
        result = subprocess.run(
            f'pg_dump "{db_url}" | gzip > "{filepath}"',
            shell=True,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()
            print(f'ERROR: pg_dump failed: {error_msg}')
            send_alert('FAILED', f'pg_dump failed with error: {error_msg}')
            # Clean up empty/failed file
            if os.path.exists(filepath):
                os.remove(filepath)
            sys.exit(1)

        # Verify the backup file exists and has content
        if not os.path.exists(filepath):
            print('ERROR: Backup file was not created')
            send_alert('FAILED', 'Backup file was not created.')
            sys.exit(1)

        file_size = os.path.getsize(filepath)
        if file_size < 100:
            print(f'ERROR: Backup file suspiciously small ({file_size} bytes)')
            send_alert('FAILED', f'Backup file is only {file_size} bytes — likely empty or corrupt.')
            os.remove(filepath)
            sys.exit(1)

        size_kb = file_size / 1024
        print(f'Backup complete: {filename} ({size_kb:.1f} KB)')

    except subprocess.TimeoutExpired:
        print('ERROR: pg_dump timed out after 5 minutes')
        send_alert('FAILED', 'pg_dump timed out after 5 minutes.')
        if os.path.exists(filepath):
            os.remove(filepath)
        sys.exit(1)
    except Exception as e:
        print(f'ERROR: Unexpected error: {e}')
        send_alert('FAILED', f'Unexpected error: {e}')
        sys.exit(1)

    # Rotate old backups — keep only the most recent N
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, 'pharmabox24_*.sql.gz')))
    if len(backups) > KEEP_BACKUPS:
        to_delete = backups[:len(backups) - KEEP_BACKUPS]
        for old_file in to_delete:
            os.remove(old_file)
            print(f'Deleted old backup: {os.path.basename(old_file)}')

    # List remaining backups
    remaining = sorted(glob.glob(os.path.join(BACKUP_DIR, 'pharmabox24_*.sql.gz')))
    print(f'\nBackups on disk ({len(remaining)}/{KEEP_BACKUPS}):')
    for f in remaining:
        size = os.path.getsize(f) / 1024
        print(f'  {os.path.basename(f)} ({size:.1f} KB)')

    print('\nBackup job complete.')


if __name__ == '__main__':
    run_backup()
