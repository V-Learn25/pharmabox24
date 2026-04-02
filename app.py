import os
import json
import secrets
from datetime import datetime, timedelta, date
from functools import wraps
from hashlib import sha256
from urllib.request import Request, urlopen
from urllib.error import URLError

from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm, CSRFProtect
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import StringField, PasswordField, SelectField, EmailField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional
from werkzeug.utils import secure_filename
from openpyxl import load_workbook
import csv

from config import Config
from models import db, User, Pharmacy, DailyStat, HourlyDistribution, Upload

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


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


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# Forms
class LoginForm(FlaskForm):
    email = EmailField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])


class UserForm(FlaskForm):
    email = EmailField('Email', validators=[DataRequired(), Email()])
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    password = PasswordField('Password', validators=[Length(min=6, max=100)])
    role = SelectField('Role', choices=[('pharmacy', 'Pharmacy User'), ('admin', 'Admin')])
    pharmacy_id = SelectField('Pharmacy', coerce=int)


class PharmacyForm(FlaskForm):
    serial_number = StringField('Serial Number', validators=[DataRequired(), Length(min=1, max=50)])
    name = StringField('Pharmacy Name', validators=[DataRequired(), Length(min=2, max=200)])
    notification_email = EmailField('Notification Email', validators=[Optional(), Email()])


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

        # Check expiry
        if datetime.now().timestamp() - timestamp > max_age:
            return None

        user = db.session.get(User, user_id)
        if not user:
            return None

        # Verify signature
        raw = f"{user.id}:{user.password_hash}:{timestamp}:{app.config['SECRET_KEY']}"
        expected = sha256(raw.encode()).hexdigest()[:32]
        if not secrets.compare_digest(signature, expected):
            return None

        return user
    except (ValueError, TypeError):
        return None


# Routes
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
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=True)
            next_page = request.args.get('next')
            flash(f'Welcome back, {user.name}!', 'success')
            return redirect(next_page or url_for('dashboard'))
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

        # Always show success (don't reveal whether email exists)
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
        # Check current password
        if not current_user.check_password(form.current_password.data):
            flash('Current password is incorrect.', 'error')
            return render_template('change_password.html', form=form)

        # Check new passwords match
        if form.new_password.data != form.confirm_password.data:
            flash('New passwords do not match.', 'error')
            return render_template('change_password.html', form=form)

        # Update password
        current_user.set_password(form.new_password.data)
        db.session.commit()
        flash('Your password has been updated successfully.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('change_password.html', form=form)


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin():
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('pharmacy_dashboard'))


# Pharmacy User Routes
@app.route('/pharmacy/dashboard')
@login_required
def pharmacy_dashboard():
    if current_user.is_admin():
        return redirect(url_for('admin_dashboard'))

    if not current_user.pharmacy_id:
        flash('Your account is not linked to a pharmacy. Please contact an administrator.', 'error')
        return render_template('pharmacy/dashboard.html', pharmacy=None, stats={})

    pharmacy = db.session.get(Pharmacy, current_user.pharmacy_id)
    today = date.today()

    # Get stats for different periods
    stats = get_pharmacy_stats(pharmacy.id, today)

    return render_template('pharmacy/dashboard.html', pharmacy=pharmacy, stats=stats)


@app.route('/api/pharmacy/chart-data')
@login_required
def pharmacy_chart_data():
    if current_user.is_admin():
        pharmacy_id = request.args.get('pharmacy_id', type=int)
    else:
        pharmacy_id = current_user.pharmacy_id

    if not pharmacy_id:
        return jsonify({'error': 'No pharmacy assigned'}), 400

    today = date.today()
    thirty_days_ago = today - timedelta(days=30)

    # Get daily data for last 30 days
    daily_data = DailyStat.query.filter(
        DailyStat.pharmacy_id == pharmacy_id,
        DailyStat.date >= thirty_days_ago
    ).order_by(DailyStat.date).all()

    # Prepare chart data
    dates = []
    loaded = []
    collected = []
    removed = []

    for stat in daily_data:
        dates.append(stat.date.strftime('%d/%m'))
        loaded.append(stat.loaded_parcels)
        collected.append(stat.collected_parcels)
        removed.append(stat.removed_parcels)

    # Day of week aggregation
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

    # Get hourly distribution data (most recent month available)
    hourly_data = HourlyDistribution.query.filter(
        HourlyDistribution.pharmacy_id == pharmacy_id
    ).order_by(HourlyDistribution.month.desc()).all()

    # Get unique months and use most recent
    hourly_by_period = {}
    if hourly_data:
        latest_month = hourly_data[0].month
        for h in hourly_data:
            if h.month == latest_month:
                hourly_by_period[h.period] = h.collected_parcels

    # Order periods correctly: 00-08, 08-12, 12-18, 18-24
    period_order = ['00-08', '08-12', '12-18', '18-24']
    hourly_labels = ['00:00-08:00', '08:00-12:00', '12:00-18:00', '18:00-24:00']
    hourly_values = [hourly_by_period.get(p, 0) for p in period_order]

    # Calculate total for percentages
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


# Admin Routes
@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    pharmacies = Pharmacy.query.all()
    today = date.today()

    # Get aggregate stats
    total_pharmacies = len(pharmacies)

    # Today's totals
    today_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.date == today).first()

    # Last 7 days totals
    seven_days_ago = today - timedelta(days=7)
    week_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.date >= seven_days_ago).first()

    # Last 30 days totals
    thirty_days_ago = today - timedelta(days=30)
    month_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels)
    ).filter(DailyStat.date >= thirty_days_ago).first()

    # Per-pharmacy summary
    pharmacy_summaries = []
    for pharmacy in pharmacies:
        summary = get_pharmacy_stats(pharmacy.id, today)
        summary['pharmacy'] = pharmacy
        pharmacy_summaries.append(summary)

    # Recent uploads
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
@admin_required
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

            # Log the upload
            upload = Upload(
                filename=filename,
                uploaded_by=current_user.id,
                records_imported=records
            )
            db.session.add(upload)
            db.session.commit()

            # Send notification emails to pharmacies with notification_email set
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
            flash(f'Error processing file: {str(e)}', 'error')

        return redirect(url_for('admin_upload'))

    return render_template('admin/upload.html', form=form, uploads=uploads)


@app.route('/admin/pharmacies')
@login_required
@admin_required
def admin_pharmacies():
    pharmacies = Pharmacy.query.order_by(Pharmacy.name).all()
    return render_template('admin/pharmacies.html', pharmacies=pharmacies)


@app.route('/admin/pharmacy/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_pharmacy_add():
    form = PharmacyForm()
    if form.validate_on_submit():
        pharmacy = Pharmacy(
            serial_number=form.serial_number.data,
            name=form.name.data,
            notification_email=form.notification_email.data or None
        )
        db.session.add(pharmacy)
        db.session.commit()
        flash(f'Pharmacy "{pharmacy.name}" has been created.', 'success')
        return redirect(url_for('admin_pharmacies'))
    return render_template('admin/pharmacy_form.html', form=form, title='Add Pharmacy')


@app.route('/admin/pharmacy/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_pharmacy_edit(id):
    pharmacy = Pharmacy.query.get_or_404(id)
    form = PharmacyForm(obj=pharmacy)
    if form.validate_on_submit():
        pharmacy.serial_number = form.serial_number.data
        pharmacy.name = form.name.data
        pharmacy.notification_email = form.notification_email.data or None
        db.session.commit()
        flash(f'Pharmacy "{pharmacy.name}" has been updated.', 'success')
        return redirect(url_for('admin_pharmacies'))
    return render_template('admin/pharmacy_form.html', form=form, title='Edit Pharmacy', pharmacy=pharmacy)


@app.route('/admin/pharmacy/<int:id>/view')
@login_required
@admin_required
def admin_pharmacy_view(id):
    pharmacy = Pharmacy.query.get_or_404(id)
    today = date.today()
    stats = get_pharmacy_stats(pharmacy.id, today)
    return render_template('admin/pharmacy_view.html', pharmacy=pharmacy, stats=stats)


@app.route('/admin/pharmacy/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_pharmacy_delete(id):
    pharmacy = Pharmacy.query.get_or_404(id)
    pharmacy_name = pharmacy.name

    # Delete related hourly distributions
    HourlyDistribution.query.filter_by(pharmacy_id=id).delete()

    # Delete related daily stats
    DailyStat.query.filter_by(pharmacy_id=id).delete()

    # Unlink users from this pharmacy (set pharmacy_id to NULL)
    User.query.filter_by(pharmacy_id=id).update({'pharmacy_id': None})

    # Delete the pharmacy
    db.session.delete(pharmacy)
    db.session.commit()

    flash(f'Pharmacy "{pharmacy_name}" and all related data have been deleted.', 'success')
    return redirect(url_for('admin_pharmacies'))


@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.name).all()
    return render_template('admin/users.html', users=users)


@app.route('/admin/user/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_user_add():
    form = UserForm()
    form.pharmacy_id.choices = [(0, '-- No Pharmacy --')] + [
        (p.id, p.name) for p in Pharmacy.query.order_by(Pharmacy.name).all()
    ]
    form.password.validators = [DataRequired(), Length(min=6, max=100)]

    if form.validate_on_submit():
        user = User(
            email=form.email.data.lower(),
            name=form.name.data,
            role=form.role.data,
            pharmacy_id=form.pharmacy_id.data if form.pharmacy_id.data != 0 else None
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f'User "{user.name}" has been created.', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', form=form, title='Add User')


@app.route('/admin/user/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_user_edit(id):
    user = User.query.get_or_404(id)
    form = UserForm(obj=user)
    form.pharmacy_id.choices = [(0, '-- No Pharmacy --')] + [
        (p.id, p.name) for p in Pharmacy.query.order_by(Pharmacy.name).all()
    ]

    if form.validate_on_submit():
        user.email = form.email.data.lower()
        user.name = form.name.data
        user.role = form.role.data
        user.pharmacy_id = form.pharmacy_id.data if form.pharmacy_id.data != 0 else None
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        flash(f'User "{user.name}" has been updated.', 'success')
        return redirect(url_for('admin_users'))

    form.pharmacy_id.data = user.pharmacy_id or 0
    return render_template('admin/user_form.html', form=form, title='Edit User', user=user)


@app.route('/admin/user/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_user_delete(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin_users'))
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{user.name}" has been deleted.', 'success')
    return redirect(url_for('admin_users'))


# Helper functions
def get_pharmacy_stats(pharmacy_id, today):
    yesterday = today - timedelta(days=1)
    seven_days_ago = today - timedelta(days=7)
    thirty_days_ago = today - timedelta(days=30)

    # Today's stats
    today_stat = DailyStat.query.filter_by(pharmacy_id=pharmacy_id, date=today).first()
    yesterday_stat = DailyStat.query.filter_by(pharmacy_id=pharmacy_id, date=yesterday).first()

    # Last 7 days
    week_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels),
        db.func.sum(DailyStat.reminders_sum)
    ).filter(
        DailyStat.pharmacy_id == pharmacy_id,
        DailyStat.date >= seven_days_ago
    ).first()

    # Last 30 days
    month_stats = db.session.query(
        db.func.sum(DailyStat.loaded_parcels),
        db.func.sum(DailyStat.collected_parcels),
        db.func.sum(DailyStat.removed_parcels),
        db.func.sum(DailyStat.reminders_sum)
    ).filter(
        DailyStat.pharmacy_id == pharmacy_id,
        DailyStat.date >= thirty_days_ago
    ).first()

    # Recent daily records
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
    affected_pharmacies = {}  # {pharmacy_id: {'loaded': X, 'collected': Y, 'removed': Z}}

    if filename.endswith('.xlsx') or filename.endswith('.xls'):
        records, affected_pharmacies = process_excel(filepath)
    else:
        records, affected_pharmacies = process_csv(filepath)

    return records, affected_pharmacies


def process_excel(filepath):
    """Process Excel file. Returns (records, affected_pharmacies)"""
    wb = load_workbook(filepath)
    records = 0
    affected_pharmacies = {}  # Track stats per pharmacy for notifications

    for sheet in wb.worksheets:
        all_rows = list(sheet.iter_rows(values_only=True))

        # Find the daily stats header row
        daily_header_row = None
        daily_headers = {}

        # Find the hourly distribution header row
        hourly_header_row = None
        hourly_headers = {}

        for row_idx, row in enumerate(all_rows):
            row_values = [str(v).lower() if v else '' for v in row]

            # Check for "Collected Parcel Distribution" section
            if 'collected parcel distribution' in ' '.join(row_values):
                # Next row should be the header for hourly data
                if row_idx + 1 < len(all_rows):
                    header_row = all_rows[row_idx + 1]
                    hourly_header_row = row_idx + 1
                    for col_idx, val in enumerate(header_row):
                        if val:
                            hourly_headers[str(val).lower().strip()] = col_idx

            # Check for daily stats header (S/N with Date column)
            elif 's/n' in row_values and 'date' in row_values:
                daily_header_row = row_idx
                for col_idx, val in enumerate(row):
                    if val:
                        daily_headers[str(val).lower().strip()] = col_idx

        # Process daily stats
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

            # Determine where daily stats end (at hourly section or end of data)
            end_row = hourly_header_row - 1 if hourly_header_row else len(all_rows)

            for row in all_rows[daily_header_row + 1:end_row]:
                if not row or col_map['serial'] < 0 or not row[col_map['serial']]:
                    continue

                serial_val = row[col_map['serial']]
                # Handle both integer and string serial numbers
                if isinstance(serial_val, (int, float)):
                    serial = str(int(serial_val))
                else:
                    serial = str(serial_val).strip()

                # Skip rows where serial number is not numeric
                if not serial.isdigit():
                    continue

                name = str(row[col_map['name']]).strip() if col_map['name'] >= 0 and row[col_map['name']] else serial

                # Get or create pharmacy
                pharmacy = Pharmacy.query.filter_by(serial_number=serial).first()
                if not pharmacy:
                    pharmacy = Pharmacy(serial_number=serial, name=name)
                    db.session.add(pharmacy)
                    db.session.flush()

                # Parse date
                date_val = row[col_map['date']]
                if isinstance(date_val, datetime):
                    stat_date = date_val.date()
                elif isinstance(date_val, date):
                    stat_date = date_val
                else:
                    continue

                # Get values
                loaded = int(row[col_map['loaded']] or 0) if col_map['loaded'] >= 0 and row[col_map['loaded']] else 0
                collected = int(row[col_map['collected']] or 0) if col_map['collected'] >= 0 and row[col_map['collected']] else 0
                removed = int(row[col_map['removed']] or 0) if col_map['removed'] >= 0 and row[col_map['removed']] else 0
                reminders = int(row[col_map['reminders']] or 0) if col_map['reminders'] >= 0 and row[col_map['reminders']] else 0

                # Upsert daily stat
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

                # Track stats for notifications
                if pharmacy.id not in affected_pharmacies:
                    affected_pharmacies[pharmacy.id] = {'loaded': 0, 'collected': 0, 'removed': 0}
                affected_pharmacies[pharmacy.id]['loaded'] += loaded
                affected_pharmacies[pharmacy.id]['collected'] += collected
                affected_pharmacies[pharmacy.id]['removed'] += removed

                records += 1

        # Process hourly distribution
        if hourly_header_row is not None:
            hourly_col_map = {
                'serial': hourly_headers.get('s/n', hourly_headers.get('serial', -1)),
                'name': hourly_headers.get('pharmacy name', hourly_headers.get('name', -1)),
                'period': hourly_headers.get('period from-to hrs', hourly_headers.get('period', -1)),
                'collected': hourly_headers.get('collected parcels', hourly_headers.get('collected', -1))
            }

            # Determine month from daily stats dates or use current month
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
                # Handle both integer and string serial numbers
                if isinstance(serial_val, (int, float)):
                    serial = str(int(serial_val))
                else:
                    serial = str(serial_val).strip()

                # Skip non-numeric serials
                if not serial.isdigit():
                    continue

                period = str(row[hourly_col_map['period']]).strip() if hourly_col_map['period'] >= 0 and row[hourly_col_map['period']] else None
                if not period:
                    continue

                collected = int(row[hourly_col_map['collected']] or 0) if hourly_col_map['collected'] >= 0 and row[hourly_col_map['collected']] else 0

                # Get or create pharmacy
                pharmacy = Pharmacy.query.filter_by(serial_number=serial).first()
                if not pharmacy:
                    name = str(row[hourly_col_map['name']]).strip() if hourly_col_map['name'] >= 0 and row[hourly_col_map['name']] else serial
                    pharmacy = Pharmacy(serial_number=serial, name=name)
                    db.session.add(pharmacy)
                    db.session.flush()

                # Upsert hourly distribution
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
        # Read all lines first
        lines = f.readlines()

        # Find the header row (look for 'S/N' or 'Pharmacy name')
        header_idx = None
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            if 's/n' in line_lower and ('pharmacy' in line_lower or 'date' in line_lower):
                header_idx = idx
                break

        if header_idx is None:
            return 0

        # Create a new file-like object starting from the header row
        from io import StringIO
        csv_content = ''.join(lines[header_idx:])
        reader = csv.DictReader(StringIO(csv_content))

        # Normalize header names
        fieldnames = {k.lower().strip(): k for k in reader.fieldnames if k}

        for row in reader:
            serial_key = fieldnames.get('s/n', fieldnames.get('serial', fieldnames.get('serial number')))
            if not serial_key or not row.get(serial_key):
                continue

            serial = str(row[serial_key]).strip()

            # Skip non-numeric serial numbers (header rows, summary rows)
            if not serial.isdigit():
                continue

            name_key = fieldnames.get('pharmacy name', fieldnames.get('name'))
            name = row.get(name_key, serial) if name_key else serial

            # Skip if name is empty or just whitespace
            if not name or not name.strip():
                name = serial

            # Get or create pharmacy
            pharmacy = Pharmacy.query.filter_by(serial_number=serial).first()
            if not pharmacy:
                pharmacy = Pharmacy(serial_number=serial, name=name.strip())
                db.session.add(pharmacy)
                db.session.flush()

            # Parse date
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

            # Get values
            loaded = int(row.get(fieldnames.get('loaded parcels', fieldnames.get('loaded', '')), 0) or 0)
            collected = int(row.get(fieldnames.get('collected parcels', fieldnames.get('collected', '')), 0) or 0)
            removed = int(row.get(fieldnames.get('removed parcels', fieldnames.get('removed', '')), 0) or 0)
            reminders = int(row.get(fieldnames.get('reminders sum', fieldnames.get('reminders', '')), 0) or 0)

            # Upsert daily stat
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

            # Track stats for notifications
            if pharmacy.id not in affected_pharmacies:
                affected_pharmacies[pharmacy.id] = {'loaded': 0, 'collected': 0, 'removed': 0}
            affected_pharmacies[pharmacy.id]['loaded'] += loaded
            affected_pharmacies[pharmacy.id]['collected'] += collected
            affected_pharmacies[pharmacy.id]['removed'] += removed

            records += 1

    db.session.commit()
    return records, affected_pharmacies


def init_db():
    """Initialize database with tables and default admin user"""
    with app.app_context():
        db.create_all()

        admin_email = os.environ.get('ADMIN_EMAIL', 'admin@pharmabox24.com')
        admin_password = os.environ.get('ADMIN_PASSWORD')

        admin = User.query.filter_by(role='admin').first()
        if not admin:
            # Create admin on first run
            admin = User(
                email=admin_email,
                name='Administrator',
                role='admin'
            )
            admin.set_password(admin_password or 'changeme123')
            db.session.add(admin)
            db.session.commit()
            print(f'Created default admin user: {admin_email}')
        elif admin_password:
            # Sync admin credentials from env vars on every startup
            admin.email = admin_email
            admin.set_password(admin_password)
            db.session.commit()
            print(f'Admin credentials synced from environment')


# Initialize DB on import (for gunicorn)
init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
