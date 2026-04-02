import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'

    # Railway uses DATABASE_URL with postgres:// but SQLAlchemy needs postgresql://
    database_url = os.environ.get('DATABASE_URL')
    if database_url and database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = database_url or \
        'sqlite:///' + os.path.join(basedir, 'pharmacy.db')

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # Email via Resend (https://resend.com)
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
    MAIL_FROM = os.environ.get('MAIL_FROM') or 'Pharmabox24 <onboarding@resend.dev>'

    # Site URL for email links
    SITE_URL = os.environ.get('SITE_URL') or 'https://neilmk.pythonanywhere.com'
