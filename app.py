import os
import json
import secrets
import logging
from collections import defaultdict
from datetime import datetime, timedelta, date
from functools import wraps
from hashlib import sha256
from urllib.request import Request, urlopen
from urllib.error import URLError

from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm, CSRFProtect
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import StringField, PasswordField, SelectField, EmailField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional
from werkzeug.utils import secure_filename
from openpyxl import load_workbook
import csv

from config import Config
from models import db, User, Organisation, Pharmacy, DailyStat, HourlyDistribution, Upload

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'


@csrf.error_handler
def csrf_error(reason):
    flash('Your session expired. Please try again.', 'error')
    return redirect(request.url)

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Simple in-memory rate limiter for login/forgot-password
_login_attempts = defaultdict(list)
_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 300  # 5 minutes


def _is_rate_limited(key):
    """Check if key has exceeded rate limit. Cleans old entries."""
    now = datetime.now().timestamp()
    _login_attempts[key] = [t for t in _login_attempts[key] if now - t < _WINDOW_SECONDS]
    return len(_login_attempts[key]) >= _MAX_ATTEMPTS


def _record_attempt(key):
    _login_attempts[key].append(datetime.now().timestamp())


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


def send_email(to_email, subject, html_content):
    """Send email via Resend API. Returns True on success."""
    api_key = app.config.get('RESEND_API_KEY')
    if not api_key:
        app.logger.warning('RESEND_API_KEY not configured - skipping email')
        return False

    try:
        payload = json.dumps({
            'from': app.config['MAIL_FROM'],
            'to': [to_email],
            'subject': subject,
            'html': html_content
        }).encode()

        req = Request(
            'https://api.resend.com/emails',
            data=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            method='POST'
        )
        urlopen(req, timeout=10)
        app.logger.info(f'Email sent to {to_email}')
        return True

    except (URLError, Exception) as e:
        app.logger.error(f'Failed to send email to {to_email}: {str(e)}')
        return False


def send_notification_email(pharmacy, stats_summary):
    """Send email notification to pharmacy about new data upload"""
    if not pharmacy.notification_email:
        return False

    site_url = app.config.get('SITE_URL', request.host_url.rstrip('/'))
    login_url = f"{site_url}/login"

    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #00891a, #006913); color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 20px; border-radius: 0 0 8px 8px; }}
        .stats-card {{ background: white; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #00891a; }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #00891a; }}
        .btn {{ display: inline-block; background: #00891a; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin-top: 15px; }}
        .footer {{ text-align: center; margin-top: 20px; color: #666; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">Pharmabox24</h1>
            <p style="margin: 5px 0 0 0;">New Statistics Available</p>
        </div>
        <div class="content">
            <p>Hello <strong>{pharmacy.name}</strong>,</p>
            <p>New statistics have been uploaded for your pharmacy. Here's a quick summary:</p>

            <div class="stats-card">
                <div>Loaded Parcels</div>
                <div class="stat-value">{stats_summary.get('loaded', 0)}</div>
            </div>
            <div class="stats-card">
                <div>Collected Parcels</div>
                <div class="stat-value">{stats_summary.get('collected', 0)}</div>
            </div>
            <div class="stats-card">
                <div>Removed Parcels</div>
                <div class="stat-value">{stats_summary.get('removed', 0)}</div>
            </div>

            <p style="text-align: center;">
                <a href="{login_url}" class="btn">View Full Analytics</a>
            </p>
        </div>
        <div class="footer">
            <p>Pharmabox24 - Prescription Collection Analytics Portal</p>
        </div>
    </div>
</body>
</html>"""

    return send_email(
        pharmacy.notification_email,
        f'New Statistics Available - {pharmacy.name}',
        html_content
    )


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# --- Decorators ---

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_super_admin():
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Allows super_admin OR org_admin (legacy 'admin' role treated as super_admin)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def _get_org_pharmacy_ids():
    """Get pharmacy IDs belonging to the current org_admin's organisation."""
    if not current_user.organisation_id:
        return []
    return [p.id for p in Pharmacy.query.filter_by(organisation_id=current_user.organisation_id).all()]


def _can_access_pharmacy(pharmacy_id):
    """Check if current user can access a given pharmacy."""
    if current_user.is_super_admin():
        return True
    if current_user.is_org_admin():
        pharmacy = db.session.get(Pharmacy, pharmacy_id)
        return pharmacy and pharmacy.organisation_id == current_user.organisation_id
    if current_user.pharmacy_id == pharmacy_id:
        return True
    return False


# Forms
class LoginForm(FlaskForm):
    email = EmailField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])


class UserForm(FlaskForm):
    email = EmailField('Email', validators=[DataRequired(), Email()])
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    password = PasswordField('Password', validators=[Length(min=6, max=100)])
    role = SelectField('Role', choices=[('pharmacy', 'Pharmacy User'), ('org_admin', 'Organisation Admin'), ('super_admin', 'Super Admin')])
    pharmacy_id = SelectField('Pharmacy', coerce=int)
    organisation_id = SelectField('Organisation', coerce=int)


class OrgUserForm(FlaskForm):
    """User form for org admins — can only create pharmacy users within their org."""
    email = EmailField('Email', validators=[DataRequired(), Email()])
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    password = PasswordField('Password', validators=[Length(min=6, max=100)])
    pharmacy_id = SelectField('Pharmacy', coerce=int)


class PharmacyForm(FlaskForm):
    serial_number = StringField('Serial Number', validators=[DataRequired(), Length(min=1, max=50)])
    name = StringField('Pharmacy Name', validators=[DataRequired(), Length(min=2, max=200)])
    notification_email = EmailField('Notification Email', validators=[Optional(), Email()])
    organisation_id = SelectField('Organisation', coerce=int)


class OrganisationForm(FlaskForm):
    name = StringField('Organisation Name', validators=[DataRequired(), Length(min=2, max=200)])


class UploadForm(FlaskForm):
    file = FileField('Data File', validators=[
        FileRequired(),
        FileAllowed(['csv', 'xlsx', 'xls'], 'CSV or Excel files only!')
    ])


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6, max=100)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired()])


class ForgotPasswordForm(FlaskForm):
    email = EmailField('Email', validators=[DataRequired(), Email()])


class ResetPasswordForm(FlaskForm):
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6, max=100)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired()])


# Password reset token helpers
def generate_reset_token(user):
    """Generate a time-limited password reset token"""
    timestamp = int(datetime.now().timestamp())
    raw = f"{user.id}:{user.password_hash}:{timestamp}:{app.config['SECRET_KEY']}"
    signature = sha256(raw.encode()).hexdigest()[:32]
    return f"{user.id}:{timestamp}:{signature}"


def verify_reset_token(token, max_age=3600):
    """Verify a password reset token (default 1 hour expiry)"""
    try:
        parts = token.split(':')
        if len(parts) != 3:
            return None
        user_id, timestamp, signature = int(parts[0]), int(parts[1]), parts[2]

        if datetime.now().timestamp() - timestamp > max_age:
            return None

        user = db.session.get(User, user_id)
        if not user:
            return None

        raw = f"{user.id}:{user.password_hash}:{timestamp}:{app.config['SECRET_KEY']}"
        expected = sha256(raw.encode()).hexdigest()[:32]
        if not secrets.compare_digest(signature, expected):
            return None

        return user
    except (ValueError, TypeError):
        return None


# === ROUTES ===

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    form = LoginForm()
    if form.validate_on_submit():
        client_ip = request.remote_addr or 'unknown'
        if _is_rate_limited(f'login:{client_ip}'):
            flash('Too many login attempts. Please wait a few minutes.', 'error')
            return render_template('login.html', form=form)

        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=True)
            next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'):
                next_page = None
            flash(f'Welcome back, {user.name}!', 'success')
            return redirect(next_page or url_for('dashboard'))
        _record_attempt(f'login:{client_ip}')
        flash('Invalid email or password.', 'error')
    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        client_ip = request.remote_addr or 'unknown'
        if _is_rate_limited(f'reset:{client_ip}'):
            flash('Too many reset requests. Please wait a few minutes.', 'error')
            return redirect(url_for('login'))
        _record_attempt(f'reset:{client_ip}')

        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user:
            token = generate_reset_token(user)
            site_url = app.config.get('SITE_URL', request.host_url.rstrip('/'))
            reset_url = f"{site_url}/reset-password/{token}"

            reset_html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #00891a, #006913); color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 20px; border-radius: 0 0 8px 8px; }}
        .btn {{ display: inline-block; background: #00891a; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin-top: 15px; }}
        .footer {{ text-align: center; margin-top: 20px; color: #666; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">Pharmabox24</h1>
            <p style="margin: 5px 0 0 0;">Password Reset</p>
        </div>
        <div class="content">
            <p>Hello <strong>{user.name}</strong>,</p>
            <p>You requested a password reset. Click the button below within 1 hour to set a new password:</p>
            <p style="text-align: center;">
                <a href="{reset_url}" class="btn">Reset Password</a>
            </p>
            <p style="font-size: 12px; color: #666;">If you didn't request this, you can safely ignore this email.</p>
        </div>
        <div class="footer">
            <p>Pharmabox24 - Prescription Collection Analytics Portal</p>
        </div>
    </div>
</body>
</html>"""
            send_email(user.email, 'Password Reset - Pharmabox24', reset_html)

        flash('If that email exists in our system, a reset link has been sent.', 'info')
        return redirect(url_for('login'))

    return render_template('forgot_password.html', form=form)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    user = verify_reset_token(token)
    if not user:
        flash('Invalid or expired reset link. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        if form.new_password.data != form.confirm_password.data:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', form=form, token=token)

        user.set_password(form.new_password.data)
        db.session.commit()
        flash('Your password has been reset. Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', form=form, token=token)


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()

    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash('Current password is incorrect.', 'error')
            return render_template('change_password.html', form=form)

        if form.new_password.data != form.confirm_password.data:
            flash('New passwords do not match.', 'error')
            return render_template('change_password.html', form=form)

        current_user.set_password(form.new_password.data)
        db.session.commit()
        flash('Your password has been updated successfully.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('change_password.html', form=form)


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_super_admin():
        return redirect(url_for('admin_dashboard'))
    if current_user.is_org_admin():
        if current_user.organisation_id:
            return redirect(url_for('org_dashboard'))
        else:
            flash('Your account is not linked to an organisation. Please contact a super admin.', 'error')
            return render_template('pharmacy/dashboard.html', pharmacy=None, stats={})
    return redirect(url_for('pharmacy_dashboard'))


# === PHARMACY USER ROUTES ===

@app.route('/pharmacy/dashboard')
@login_required
def pharmacy_dashboard():
    if current_user.is_super_admin():
        return redirect(url_for('admin_dashboard'))
    if current_user.is_org_admin():
        return redirect(url_for('org_dashboard'))

    if not current_user.pharmacy_id:
        flash('Your account is not linked to a pharmacy. Please contact an administrator.', 'error')
        return render_template('pharmacy/dashboard.html', pharmacy=None, stats={})

    pharmacy = db.session.get(Pharmacy, current_user.pharmacy_id)
    today = date.today()
    stats = get_pharmacy_stats(pharmacy.id, today)

    return render_template('pharmacy/dashboard.html', pharmacy=pharmacy, stats=stats)


@app.route('/api/pharmacy/chart-data')
@login_required
def pharmacy_chart_data():
    if current_user.is_super_admin() or current_user.is_org_admin():
        pharmacy_id = request.args.get('pharmacy_id', type=int)
    else:
        pharmacy_id = current_user.pharmacy_id

    if not pharmacy_id:
        return jsonify({'error': 'No pharmacy assigned'}), 400

    # Access check for org_admin
    if current_user.is_org_admin() and not _can_access_pharmacy(pharmacy_id):
        return jsonify({'error': 'Access denied'}), 403

    today = date.today()
    thirty_days_ago = today - timedelta(days=30)

    daily_data = DailyStat.query.filter(
        DailyStat.pharmacy_id == pharmacy_id,
        DailyStat.date >= thirty_days_ago
    ).order_by(DailyStat.date).all()

    dates = []
    loaded = []
    collected = []
    removed = []

    for stat in daily_data:
        dates.append(stat.date.strftime('%d/%m'))
        loaded.append(stat.loaded_parcels)
        collected.append(stat.collected_parcels)
        removed.append(stat.removed_parcels)

    day_of_week = {i: {'loaded': 0, 'collected': 0, 'count': 0} for i in range(7)}
    for stat in daily_data:
        dow = stat.date.weekday()
        day_of_week[dow]['loaded'] += stat.loaded_parcels
        day_of_week[dow]['collected'] += stat.collected_parcels
        day_of_week[dow]['count'] += 1

    dow_labels = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    dow_loaded = []
    dow_collected = []
    for i in range(7):
        count = day_of_week[i]['count'] or 1
        dow_loaded.append(round(day_of_week[i]['loaded'] / count, 1))
        dow_collected.append(round(day_of_week[i]['collected'] / count, 1))

    hourly_data = HourlyDistribution.query.filter(
        HourlyDistribution.pharmacy_id == pharmacy_id
    ).order_by(HourlyDistribution.month.desc()).all()

    hourly_by_period = {}
    if hourly_data:
        latest_month = hourly_data[0].month
        for h in hourly_data:
            if h.month == latest_month:
                hourly_by_period[h.period] = h.collected_parcels

    period_order = ['00-08', '08-12', '12-18', '18-24']
    hourly_labels = ['00:00-08:00', '08:00-12:00', '12:00-18:00', '18:00-24:00']
    hourly_values = [hourly_by_period.get(p, 0) for p in period_order]
    hourly_total = sum(hourly_values) or 1
    hourly_percentages = [round((v / hourly_total) * 100, 1) for v in hourly_values]

    return jsonify({
        'daily': {
            'labels': dates,
            'loaded': loaded,
            'collected': collected,
            'removed': removed
        },
        'dayOfWeek': {
            'labels': dow_labels,
            'loaded': dow_loaded,
            'collected': dow_collected
        },
        'hourly': {
            'labels': hourly_labels,
            'values': hourly_values,
            'percentages': hourly_percentages
        }
    })


# === ORGANISATION ADMIN ROUTES ===

@app.route('/org/dashboard')
@login_required
def org_dashboard():
    if not current_user.is_org_admin():
        return redirect(url_for('dashboard'))

    org = db.session.get(Organisation, current_user.organisation_id)
    if not org:
        flash('Your account is not linked to an organisation.', 'error')
        return redirect(url_for('dashboard'))

    pharmacies = Pharmacy.query.filter_by(organisation_id=org.id).order_by(Pharmacy.name).all()
    today = date.today()

    pharmacy_ids = [p.id for p in pharmacies]

    # Aggregate stats across org pharmacies
    today_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.pharmacy_id.in_(pharmacy_ids), DailyStat.date == today).first() if pharmacy_ids else (0, 0, 0)

    seven_days_ago = today - timedelta(days=7)
    week_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.pharmacy_id.in_(pharmacy_ids), DailyStat.date >= seven_days_ago).first() if pharmacy_ids else (0, 0, 0)

    thirty_days_ago = today - timedelta(days=30)
    month_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.pharmacy_id.in_(pharmacy_ids), DailyStat.date >= thirty_days_ago).first() if pharmacy_ids else (0, 0, 0)

    pharmacy_summaries = []
    for pharmacy in pharmacies:
        summary = get_pharmacy_stats(pharmacy.id, today)
        summary['pharmacy'] = pharmacy
        pharmacy_summaries.append(summary)

    stats = {
        'total_pharmacies': len(pharmacies),
        'today': {
            'loaded': (today_stats[0] or 0) if today_stats else 0,
            'collected': (today_stats[1] or 0) if today_stats else 0,
            'removed': (today_stats[2] or 0) if today_stats else 0
        },
        'week': {
            'loaded': (week_stats[0] or 0) if week_stats else 0,
            'collected': (week_stats[1] or 0) if week_stats else 0,
            'removed': (week_stats[2] or 0) if week_stats else 0
        },
        'month': {
            'loaded': (month_stats[0] or 0) if month_stats else 0,
            'collected': (month_stats[1] or 0) if month_stats else 0,
            'removed': (month_stats[2] or 0) if month_stats else 0
        }
    }

    return render_template('org/dashboard.html', org=org, stats=stats, pharmacy_summaries=pharmacy_summaries)


@app.route('/org/pharmacy/<int:id>/view')
@login_required
def org_pharmacy_view(id):
    if not current_user.is_org_admin():
        return redirect(url_for('dashboard'))

    pharmacy = Pharmacy.query.get_or_404(id)
    if pharmacy.organisation_id != current_user.organisation_id:
        abort(403)

    today = date.today()
    stats = get_pharmacy_stats(pharmacy.id, today)
    return render_template('org/pharmacy_view.html', pharmacy=pharmacy, stats=stats)


@app.route('/org/users')
@login_required
def org_users():
    if not current_user.is_org_admin():
        return redirect(url_for('dashboard'))

    org_pharmacy_ids = _get_org_pharmacy_ids()
    # Show org_admin users for this org + pharmacy users linked to org pharmacies
    users = User.query.filter(
        db.or_(
            User.organisation_id == current_user.organisation_id,
            User.pharmacy_id.in_(org_pharmacy_ids) if org_pharmacy_ids else db.false()
        )
    ).order_by(User.name).all()
    return render_template('org/users.html', users=users)


@app.route('/org/user/add', methods=['GET', 'POST'])
@login_required
def org_user_add():
    if not current_user.is_org_admin():
        return redirect(url_for('dashboard'))

    form = OrgUserForm()
    org_pharmacies = Pharmacy.query.filter_by(organisation_id=current_user.organisation_id).order_by(Pharmacy.name).all()
    form.pharmacy_id.choices = [(0, '-- No Pharmacy --')] + [(p.id, p.name) for p in org_pharmacies]
    form.password.validators = [DataRequired(), Length(min=6, max=100)]

    if form.validate_on_submit():
        existing = User.query.filter_by(email=form.email.data.lower()).first()
        if existing:
            flash('A user with that email already exists.', 'error')
            return render_template('org/user_form.html', form=form, title='Add User')

        user = User(
            email=form.email.data.lower(),
            name=form.name.data,
            role='pharmacy',
            pharmacy_id=form.pharmacy_id.data if form.pharmacy_id.data != 0 else None,
            organisation_id=current_user.organisation_id
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f'User "{user.name}" has been created.', 'success')
        return redirect(url_for('org_users'))
    return render_template('org/user_form.html', form=form, title='Add User')


@app.route('/org/user/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def org_user_edit(id):
    if not current_user.is_org_admin():
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(id)
    # Can only edit users in their org
    org_pharmacy_ids = _get_org_pharmacy_ids()
    if user.organisation_id != current_user.organisation_id and user.pharmacy_id not in org_pharmacy_ids:
        abort(403)

    form = OrgUserForm(obj=user)
    org_pharmacies = Pharmacy.query.filter_by(organisation_id=current_user.organisation_id).order_by(Pharmacy.name).all()
    form.pharmacy_id.choices = [(0, '-- No Pharmacy --')] + [(p.id, p.name) for p in org_pharmacies]

    if form.validate_on_submit():
        new_email = form.email.data.lower()
        if new_email != user.email:
            existing = User.query.filter_by(email=new_email).first()
            if existing:
                flash('A user with that email already exists.', 'error')
                form.pharmacy_id.data = user.pharmacy_id or 0
                return render_template('org/user_form.html', form=form, title='Edit User', user=user)

        user.email = new_email
        user.name = form.name.data
        user.pharmacy_id = form.pharmacy_id.data if form.pharmacy_id.data != 0 else None
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        flash(f'User "{user.name}" has been updated.', 'success')
        return redirect(url_for('org_users'))

    form.pharmacy_id.data = user.pharmacy_id or 0
    return render_template('org/user_form.html', form=form, title='Edit User', user=user)


@app.route('/org/user/<int:id>/delete', methods=['POST'])
@login_required
def org_user_delete(id):
    if not current_user.is_org_admin():
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('org_users'))

    # Can only delete users in their org
    org_pharmacy_ids = _get_org_pharmacy_ids()
    if user.organisation_id != current_user.organisation_id and user.pharmacy_id not in org_pharmacy_ids:
        abort(403)

    db.session.delete(user)
    db.session.commit()
    flash(f'User "{user.name}" has been deleted.', 'success')
    return redirect(url_for('org_users'))


# === SUPER ADMIN ROUTES ===

@app.route('/admin/dashboard')
@login_required
@super_admin_required
def admin_dashboard():
    pharmacies = Pharmacy.query.all()
    today = date.today()

    total_pharmacies = len(pharmacies)

    today_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.date == today).first()

    seven_days_ago = today - timedelta(days=7)
    week_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.date >= seven_days_ago).first()

    thirty_days_ago = today - timedelta(days=30)
    month_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.date >= thirty_days_ago).first()

    pharmacy_summaries = []
    for pharmacy in pharmacies:
        summary = get_pharmacy_stats(pharmacy.id, today)
        summary['pharmacy'] = pharmacy
        pharmacy_summaries.append(summary)

    recent_uploads = Upload.query.order_by(Upload.uploaded_at.desc()).limit(5).all()

    stats = {
        'total_pharmacies': total_pharmacies,
        'today': {
            'loaded': today_stats[0] or 0,
            'collected': today_stats[1] or 0,
            'removed': today_stats[2] or 0
        },
        'week': {
            'loaded': week_stats[0] or 0,
            'collected': week_stats[1] or 0,
            'removed': week_stats[2] or 0
        },
        'month': {
            'loaded': month_stats[0] or 0,
            'collected': month_stats[1] or 0,
            'removed': month_stats[2] or 0
        }
    }

    return render_template('admin/dashboard.html',
                         stats=stats,
                         pharmacy_summaries=pharmacy_summaries,
                         recent_uploads=recent_uploads)


@app.route('/admin/upload', methods=['GET', 'POST'])
@login_required
@super_admin_required
def admin_upload():
    form = UploadForm()
    uploads = Upload.query.order_by(Upload.uploaded_at.desc()).limit(20).all()

    if form.validate_on_submit():
        file = form.file.data
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            records, affected_pharmacies = process_upload(filepath, filename)

            if records == 0:
                flash('No records found in file. Please check the file format matches the expected template.', 'error')
            else:
                upload = Upload(
                    filename=filename,
                    uploaded_by=current_user.id,
                    records_imported=records
                )
                db.session.add(upload)
                db.session.commit()

                emails_sent = 0
                for pharmacy_id, stats in affected_pharmacies.items():
                    pharmacy = db.session.get(Pharmacy, pharmacy_id)
                    if pharmacy and pharmacy.notification_email:
                        if send_notification_email(pharmacy, stats):
                            emails_sent += 1

                flash(f'Successfully imported {records} records from {filename}', 'success')
                if emails_sent > 0:
                    flash(f'Sent {emails_sent} notification email(s) to pharmacies', 'info')

        except Exception as e:
            db.session.rollback()
            logger.exception(f'Error processing upload {filename}')
            flash(f'Error processing file: {str(e)}', 'error')
        finally:
            try:
                os.remove(filepath)
            except OSError:
                pass

        return redirect(url_for('admin_upload'))

    return render_template('admin/upload.html', form=form, uploads=uploads)


# --- Organisation CRUD (super admin only) ---

@app.route('/admin/organisations')
@login_required
@super_admin_required
def admin_organisations():
    organisations = Organisation.query.order_by(Organisation.name).all()
    return render_template('admin/organisations.html', organisations=organisations)


@app.route('/admin/organisation/add', methods=['GET', 'POST'])
@login_required
@super_admin_required
def admin_organisation_add():
    form = OrganisationForm()
    if form.validate_on_submit():
        org = Organisation(name=form.name.data)
        db.session.add(org)
        db.session.commit()
        flash(f'Organisation "{org.name}" has been created.', 'success')
        return redirect(url_for('admin_organisations'))
    return render_template('admin/organisation_form.html', form=form, title='Add Organisation')


@app.route('/admin/organisation/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@super_admin_required
def admin_organisation_edit(id):
    org = Organisation.query.get_or_404(id)
    form = OrganisationForm(obj=org)
    if form.validate_on_submit():
        org.name = form.name.data
        db.session.commit()
        flash(f'Organisation "{org.name}" has been updated.', 'success')
        return redirect(url_for('admin_organisations'))
    return render_template('admin/organisation_form.html', form=form, title='Edit Organisation', org=org)


@app.route('/admin/organisation/<int:id>/delete', methods=['POST'])
@login_required
@super_admin_required
def admin_organisation_delete(id):
    org = Organisation.query.get_or_404(id)
    # Unlink pharmacies and users
    Pharmacy.query.filter_by(organisation_id=id).update({'organisation_id': None})
    User.query.filter_by(organisation_id=id).update({'organisation_id': None})
    db.session.delete(org)
    db.session.commit()
    flash(f'Organisation "{org.name}" has been deleted.', 'success')
    return redirect(url_for('admin_organisations'))


# --- Pharmacy CRUD (super admin) ---

@app.route('/admin/pharmacies')
@login_required
@super_admin_required
def admin_pharmacies():
    pharmacies = Pharmacy.query.order_by(Pharmacy.name).all()
    return render_template('admin/pharmacies.html', pharmacies=pharmacies)


@app.route('/admin/pharmacy/add', methods=['GET', 'POST'])
@login_required
@super_admin_required
def admin_pharmacy_add():
    form = PharmacyForm()
    form.organisation_id.choices = [(0, '-- No Organisation --')] + [
        (o.id, o.name) for o in Organisation.query.order_by(Organisation.name).all()
    ]
    if form.validate_on_submit():
        existing = Pharmacy.query.filter_by(serial_number=form.serial_number.data).first()
        if existing:
            flash(f'A pharmacy with serial number "{form.serial_number.data}" already exists.', 'error')
            return render_template('admin/pharmacy_form.html', form=form, title='Add Pharmacy')

        pharmacy = Pharmacy(
            serial_number=form.serial_number.data,
            name=form.name.data,
            notification_email=form.notification_email.data or None,
            organisation_id=form.organisation_id.data if form.organisation_id.data != 0 else None
        )
        db.session.add(pharmacy)
        db.session.commit()
        flash(f'Pharmacy "{pharmacy.name}" has been created.', 'success')
        return redirect(url_for('admin_pharmacies'))
    return render_template('admin/pharmacy_form.html', form=form, title='Add Pharmacy')


@app.route('/admin/pharmacy/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@super_admin_required
def admin_pharmacy_edit(id):
    pharmacy = Pharmacy.query.get_or_404(id)
    form = PharmacyForm(obj=pharmacy)
    form.organisation_id.choices = [(0, '-- No Organisation --')] + [
        (o.id, o.name) for o in Organisation.query.order_by(Organisation.name).all()
    ]
    if form.validate_on_submit():
        pharmacy.serial_number = form.serial_number.data
        pharmacy.name = form.name.data
        pharmacy.notification_email = form.notification_email.data or None
        pharmacy.organisation_id = form.organisation_id.data if form.organisation_id.data != 0 else None
        db.session.commit()
        flash(f'Pharmacy "{pharmacy.name}" has been updated.', 'success')
        return redirect(url_for('admin_pharmacies'))
    form.organisation_id.data = pharmacy.organisation_id or 0
    return render_template('admin/pharmacy_form.html', form=form, title='Edit Pharmacy', pharmacy=pharmacy)


@app.route('/admin/pharmacy/<int:id>/view')
@login_required
@super_admin_required
def admin_pharmacy_view(id):
    pharmacy = Pharmacy.query.get_or_404(id)
    today = date.today()
    stats = get_pharmacy_stats(pharmacy.id, today)
    return render_template('admin/pharmacy_view.html', pharmacy=pharmacy, stats=stats)


@app.route('/admin/pharmacy/<int:id>/delete', methods=['POST'])
@login_required
@super_admin_required
def admin_pharmacy_delete(id):
    pharmacy = Pharmacy.query.get_or_404(id)
    pharmacy_name = pharmacy.name

    HourlyDistribution.query.filter_by(pharmacy_id=id).delete()
    DailyStat.query.filter_by(pharmacy_id=id).delete()
    User.query.filter_by(pharmacy_id=id).update({'pharmacy_id': None})
    db.session.delete(pharmacy)
    db.session.commit()

    flash(f'Pharmacy "{pharmacy_name}" and all related data have been deleted.', 'success')
    return redirect(url_for('admin_pharmacies'))


# --- User CRUD (super admin) ---

@app.route('/admin/users')
@login_required
@super_admin_required
def admin_users():
    users = User.query.order_by(User.name).all()
    return render_template('admin/users.html', users=users)


@app.route('/admin/user/add', methods=['GET', 'POST'])
@login_required
@super_admin_required
def admin_user_add():
    form = UserForm()
    form.pharmacy_id.choices = [(0, '-- No Pharmacy --')] + [
        (p.id, p.name) for p in Pharmacy.query.order_by(Pharmacy.name).all()
    ]
    form.organisation_id.choices = [(0, '-- No Organisation --')] + [
        (o.id, o.name) for o in Organisation.query.order_by(Organisation.name).all()
    ]
    form.password.validators = [DataRequired(), Length(min=6, max=100)]

    if form.validate_on_submit():
        existing = User.query.filter_by(email=form.email.data.lower()).first()
        if existing:
            flash('A user with that email already exists.', 'error')
            return render_template('admin/user_form.html', form=form, title='Add User')

        user = User(
            email=form.email.data.lower(),
            name=form.name.data,
            role=form.role.data,
            pharmacy_id=form.pharmacy_id.data if form.pharmacy_id.data != 0 else None,
            organisation_id=form.organisation_id.data if form.organisation_id.data != 0 else None
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f'User "{user.name}" has been created.', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', form=form, title='Add User')


@app.route('/admin/user/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@super_admin_required
def admin_user_edit(id):
    user = User.query.get_or_404(id)
    form = UserForm(obj=user)
    form.pharmacy_id.choices = [(0, '-- No Pharmacy --')] + [
        (p.id, p.name) for p in Pharmacy.query.order_by(Pharmacy.name).all()
    ]
    form.organisation_id.choices = [(0, '-- No Organisation --')] + [
        (o.id, o.name) for o in Organisation.query.order_by(Organisation.name).all()
    ]

    if form.validate_on_submit():
        new_email = form.email.data.lower()
        if new_email != user.email:
            existing = User.query.filter_by(email=new_email).first()
            if existing:
                flash('A user with that email already exists.', 'error')
                form.pharmacy_id.data = user.pharmacy_id or 0
                form.organisation_id.data = user.organisation_id or 0
                return render_template('admin/user_form.html', form=form, title='Edit User', user=user)

        user.email = new_email
        user.name = form.name.data
        user.role = form.role.data
        user.pharmacy_id = form.pharmacy_id.data if form.pharmacy_id.data != 0 else None
        user.organisation_id = form.organisation_id.data if form.organisation_id.data != 0 else None
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        flash(f'User "{user.name}" has been updated.', 'success')
        return redirect(url_for('admin_users'))

    form.pharmacy_id.data = user.pharmacy_id or 0
    form.organisation_id.data = user.organisation_id or 0
    return render_template('admin/user_form.html', form=form, title='Edit User', user=user)


@app.route('/admin/user/<int:id>/delete', methods=['POST'])
@login_required
@super_admin_required
def admin_user_delete(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin_users'))
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{user.name}" has been deleted.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/help')
@login_required
@admin_required
def admin_help():
    return render_template('admin/help.html')


# === HELPER FUNCTIONS ===

def get_pharmacy_stats(pharmacy_id, today):
    yesterday = today - timedelta(days=1)
    seven_days_ago = today - timedelta(days=7)
    thirty_days_ago = today - timedelta(days=30)

    today_stat = DailyStat.query.filter_by(pharmacy_id=pharmacy_id, date=today).first()
    yesterday_stat = DailyStat.query.filter_by(pharmacy_id=pharmacy_id, date=yesterday).first()

    week_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels),
        db.func.sum(DailyStat.reminders_sum)
    ).filter(
        DailyStat.pharmacy_id == pharmacy_id,
        DailyStat.date >= seven_days_ago
    ).first()

    month_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels),
        db.func.sum(DailyStat.reminders_sum)
    ).filter(
        DailyStat.pharmacy_id == pharmacy_id,
        DailyStat.date >= thirty_days_ago
    ).first()

    recent_records = DailyStat.query.filter_by(pharmacy_id=pharmacy_id)\
        .order_by(DailyStat.date.desc()).limit(30).all()

    return {
        'today': {
            'loaded': today_stat.loaded_parcels if today_stat else 0,
            'collected': today_stat.collected_parcels if today_stat else 0,
            'removed': today_stat.removed_parcels if today_stat else 0,
            'reminders': today_stat.reminders_sum if today_stat else 0
        },
        'yesterday': {
            'loaded': yesterday_stat.loaded_parcels if yesterday_stat else 0,
            'collected': yesterday_stat.collected_parcels if yesterday_stat else 0,
            'removed': yesterday_stat.removed_parcels if yesterday_stat else 0,
            'reminders': yesterday_stat.reminders_sum if yesterday_stat else 0
        },
        'week': {
            'loaded': week_stats[0] or 0,
            'collected': week_stats[1] or 0,
            'removed': week_stats[2] or 0,
            'reminders': week_stats[3] or 0
        },
        'month': {
            'loaded': month_stats[0] or 0,
            'collected': month_stats[1] or 0,
            'removed': month_stats[2] or 0,
            'reminders': month_stats[3] or 0
        },
        'recent_records': recent_records
    }


def process_upload(filepath, filename):
    """Process CSV or Excel file and import data. Returns (records, affected_pharmacies)"""
    records = 0
    affected_pharmacies = {}

    if filename.endswith('.xlsx') or filename.endswith('.xls'):
        records, affected_pharmacies = process_excel(filepath)
    else:
        records, affected_pharmacies = process_csv(filepath)

    return records, affected_pharmacies


def process_excel(filepath):
    """Process Excel file. Returns (records, affected_pharmacies)"""
    wb = load_workbook(filepath)
    records = 0
    affected_pharmacies = {}

    for sheet in wb.worksheets:
        all_rows = list(sheet.iter_rows(values_only=True))

        daily_header_row = None
        daily_headers = {}
        hourly_header_row = None
        hourly_headers = {}

        for row_idx, row in enumerate(all_rows):
            row_values = [str(v).lower() if v else '' for v in row]

            if 'collected parcel distribution' in ' '.join(row_values):
                if row_idx + 1 < len(all_rows):
                    header_row = all_rows[row_idx + 1]
                    hourly_header_row = row_idx + 1
                    for col_idx, val in enumerate(header_row):
                        if val:
                            hourly_headers[str(val).lower().strip()] = col_idx

            elif 's/n' in row_values and 'date' in row_values:
                daily_header_row = row_idx
                for col_idx, val in enumerate(row):
                    if val:
                        daily_headers[str(val).lower().strip()] = col_idx

        if daily_header_row is not None:
            col_map = {
                'serial': daily_headers.get('s/n', daily_headers.get('serial', -1)),
                'name': daily_headers.get('pharmacy name', daily_headers.get('name', -1)),
                'date': daily_headers.get('date', -1),
                'loaded': daily_headers.get('loaded parcels', daily_headers.get('loaded', -1)),
                'collected': daily_headers.get('collected parcels', daily_headers.get('collected', -1)),
                'removed': daily_headers.get('removed parcels', daily_headers.get('removed', -1)),
                'reminders': daily_headers.get('reminders sum', daily_headers.get('reminders', -1))
            }

            end_row = hourly_header_row - 1 if hourly_header_row else len(all_rows)

            for row in all_rows[daily_header_row + 1:end_row]:
                if not row or col_map['serial'] < 0 or not row[col_map['serial']]:
                    continue

                serial_val = row[col_map['serial']]
                if isinstance(serial_val, (int, float)):
                    serial = str(int(serial_val))
                else:
                    serial = str(serial_val).strip()

                if not serial.isdigit():
                    continue

                name = str(row[col_map['name']]).strip() if col_map['name'] >= 0 and row[col_map['name']] else serial

                pharmacy = Pharmacy.query.filter_by(serial_number=serial).first()
                if not pharmacy:
                    pharmacy = Pharmacy(serial_number=serial, name=name)
                    db.session.add(pharmacy)
                    db.session.flush()

                date_val = row[col_map['date']]
                if isinstance(date_val, datetime):
                    stat_date = date_val.date()
                elif isinstance(date_val, date):
                    stat_date = date_val
                else:
                    continue

                loaded = int(row[col_map['loaded']] or 0) if col_map['loaded'] >= 0 and row[col_map['loaded']] else 0
                collected = int(row[col_map['collected']] or 0) if col_map['collected'] >= 0 and row[col_map['collected']] else 0
                removed = int(row[col_map['removed']] or 0) if col_map['removed'] >= 0 and row[col_map['removed']] else 0
                reminders = int(row[col_map['reminders']] or 0) if col_map['reminders'] >= 0 and row[col_map['reminders']] else 0

                stat = DailyStat.query.filter_by(pharmacy_id=pharmacy.id, date=stat_date).first()
                if stat:
                    stat.loaded_parcels = loaded
                    stat.collected_parcels = collected
                    stat.removed_parcels = removed
                    stat.reminders_sum = reminders
                else:
                    stat = DailyStat(
                        pharmacy_id=pharmacy.id,
                        date=stat_date,
                        loaded_parcels=loaded,
                        collected_parcels=collected,
                        removed_parcels=removed,
                        reminders_sum=reminders
                    )
                    db.session.add(stat)

                if pharmacy.id not in affected_pharmacies:
                    affected_pharmacies[pharmacy.id] = {'loaded': 0, 'collected': 0, 'removed': 0}
                affected_pharmacies[pharmacy.id]['loaded'] += loaded
                affected_pharmacies[pharmacy.id]['collected'] += collected
                affected_pharmacies[pharmacy.id]['removed'] += removed

                records += 1

        if hourly_header_row is not None:
            hourly_col_map = {
                'serial': hourly_headers.get('s/n', hourly_headers.get('serial', -1)),
                'name': hourly_headers.get('pharmacy name', hourly_headers.get('name', -1)),
                'period': hourly_headers.get('period from-to hrs', hourly_headers.get('period', -1)),
                'collected': hourly_headers.get('collected parcels', hourly_headers.get('collected', -1))
            }

            report_month = date.today().replace(day=1)
            if daily_header_row is not None:
                for row in all_rows[daily_header_row + 1:]:
                    if row and col_map['date'] >= 0 and row[col_map['date']]:
                        date_val = row[col_map['date']]
                        if isinstance(date_val, datetime):
                            report_month = date_val.date().replace(day=1)
                            break
                        elif isinstance(date_val, date):
                            report_month = date_val.replace(day=1)
                            break

            for row in all_rows[hourly_header_row + 1:]:
                if not row or hourly_col_map['serial'] < 0 or not row[hourly_col_map['serial']]:
                    continue

                serial_val = row[hourly_col_map['serial']]
                if isinstance(serial_val, (int, float)):
                    serial = str(int(serial_val))
                else:
                    serial = str(serial_val).strip()

                if not serial.isdigit():
                    continue

                period = str(row[hourly_col_map['period']]).strip() if hourly_col_map['period'] >= 0 and row[hourly_col_map['period']] else None
                if not period:
                    continue

                collected = int(row[hourly_col_map['collected']] or 0) if hourly_col_map['collected'] >= 0 and row[hourly_col_map['collected']] else 0

                pharmacy = Pharmacy.query.filter_by(serial_number=serial).first()
                if not pharmacy:
                    name = str(row[hourly_col_map['name']]).strip() if hourly_col_map['name'] >= 0 and row[hourly_col_map['name']] else serial
                    pharmacy = Pharmacy(serial_number=serial, name=name)
                    db.session.add(pharmacy)
                    db.session.flush()

                hourly = HourlyDistribution.query.filter_by(
                    pharmacy_id=pharmacy.id,
                    period=period,
                    month=report_month
                ).first()

                if hourly:
                    hourly.collected_parcels = collected
                else:
                    hourly = HourlyDistribution(
                        pharmacy_id=pharmacy.id,
                        period=period,
                        collected_parcels=collected,
                        month=report_month
                    )
                    db.session.add(hourly)

    db.session.commit()
    return records, affected_pharmacies


def process_csv(filepath):
    """Process CSV file. Returns (records, affected_pharmacies)"""
    records = 0
    affected_pharmacies = {}

    with open(filepath, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()

        header_idx = None
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            if 's/n' in line_lower and ('pharmacy' in line_lower or 'date' in line_lower):
                header_idx = idx
                break

        if header_idx is None:
            return 0, {}

        from io import StringIO
        csv_content = ''.join(lines[header_idx:])
        reader = csv.DictReader(StringIO(csv_content))

        fieldnames = {k.lower().strip(): k for k in reader.fieldnames if k}

        for row in reader:
            serial_key = fieldnames.get('s/n', fieldnames.get('serial', fieldnames.get('serial number')))
            if not serial_key or not row.get(serial_key):
                continue

            serial = str(row[serial_key]).strip()

            if not serial.isdigit():
                continue

            name_key = fieldnames.get('pharmacy name', fieldnames.get('name'))
            name = row.get(name_key, serial) if name_key else serial

            if not name or not name.strip():
                name = serial

            pharmacy = Pharmacy.query.filter_by(serial_number=serial).first()
            if not pharmacy:
                pharmacy = Pharmacy(serial_number=serial, name=name.strip())
                db.session.add(pharmacy)
                db.session.flush()

            date_key = fieldnames.get('date')
            if not date_key or not row.get(date_key):
                continue

            date_str = row[date_key].strip()
            if not date_str:
                continue

            try:
                stat_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                try:
                    stat_date = datetime.strptime(date_str, '%d/%m/%Y').date()
                except ValueError:
                    continue

            loaded = int(row.get(fieldnames.get('loaded parcels', fieldnames.get('loaded', '')), 0) or 0)
            collected = int(row.get(fieldnames.get('collected parcels', fieldnames.get('collected', '')), 0) or 0)
            removed = int(row.get(fieldnames.get('removed parcels', fieldnames.get('removed', '')), 0) or 0)
            reminders = int(row.get(fieldnames.get('reminders sum', fieldnames.get('reminders', '')), 0) or 0)

            stat = DailyStat.query.filter_by(pharmacy_id=pharmacy.id, date=stat_date).first()
            if stat:
                stat.loaded_parcels = loaded
                stat.collected_parcels = collected
                stat.removed_parcels = removed
                stat.reminders_sum = reminders
            else:
                stat = DailyStat(
                    pharmacy_id=pharmacy.id,
                    date=stat_date,
                    loaded_parcels=loaded,
                    collected_parcels=collected,
                    removed_parcels=removed,
                    reminders_sum=reminders
                )
                db.session.add(stat)

            if pharmacy.id not in affected_pharmacies:
                affected_pharmacies[pharmacy.id] = {'loaded': 0, 'collected': 0, 'removed': 0}
            affected_pharmacies[pharmacy.id]['loaded'] += loaded
            affected_pharmacies[pharmacy.id]['collected'] += collected
            affected_pharmacies[pharmacy.id]['removed'] += removed

            records += 1

    db.session.commit()
    return records, affected_pharmacies


def init_db():
    """Initialize database with tables and default super admin user"""
    with app.app_context():
        db.create_all()

        admin_email = os.environ.get('ADMIN_EMAIL', 'admin@pharmabox24.com').strip().lower()
        admin_password = os.environ.get('ADMIN_PASSWORD', '').strip()

        if admin_password:
            # Migrate existing 'admin' role users to 'super_admin'
            User.query.filter_by(role='admin').update({'role': 'super_admin'})
            db.session.commit()

            # Force-sync super admin
            admin = User.query.filter_by(role='super_admin').first()
            if not admin:
                admin = User.query.filter_by(email=admin_email).first()
            if admin:
                admin.email = admin_email
                admin.role = 'super_admin'
                admin.set_password(admin_password)
                db.session.commit()
                print(f'Super admin credentials synced: {admin_email}')
            else:
                admin = User(
                    email=admin_email,
                    name='Administrator',
                    role='super_admin'
                )
                admin.set_password(admin_password)
                db.session.add(admin)
                db.session.commit()
                print(f'Created super admin user: {admin_email}')
        else:
            if not User.query.filter_by(role='super_admin').first():
                # Also check for legacy 'admin' role
                legacy = User.query.filter_by(role='admin').first()
                if legacy:
                    legacy.role = 'super_admin'
                    db.session.commit()
                    print(f'Migrated admin to super_admin: {legacy.email}')
                else:
                    admin = User(
                        email=admin_email,
                        name='Administrator',
                        role='super_admin'
                    )
                    admin.set_password('changeme123')
                    db.session.add(admin)
                    db.session.commit()
                    print(f'Created default super admin: {admin_email} / changeme123')


# Initialize DB on import (for gunicorn)
init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
