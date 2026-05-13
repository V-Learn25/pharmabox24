from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Organisation(db.Model):
    __tablename__ = 'organisations'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    pharmacies = db.relationship('Pharmacy', backref='organisation', lazy='dynamic')
    users = db.relationship('User', backref='organisation', lazy='dynamic')

    def __repr__(self):
        return f'<Organisation {self.name}>'


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='pharmacy')  # 'super_admin', 'org_admin', 'pharmacy'
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacies.id'), nullable=True, index=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey('organisations.id'), nullable=True, index=True)
    # Bumped on password change / forced logout to invalidate outstanding sessions and reset tokens.
    session_version = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # Set the first time a user completes the /setup-account flow. Null = invitation
    # sent but not yet accepted. Distinct from created_at, which fires when the admin
    # creates the row.
    activated_at = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)

    pharmacy = db.relationship('Pharmacy', backref='users')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        # Invalidate every outstanding session and reset token tied to this user.
        self.session_version = (self.session_version or 1) + 1

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        """Bind session cookie to session_version — bumping it logs the user out everywhere."""
        return f'{self.id}|{self.session_version or 1}'

    def is_super_admin(self):
        # Legacy 'admin' role kept as a safety net until init_db migration is verified across all envs.
        return self.role in ('super_admin', 'admin')

    def is_org_admin(self):
        return self.role == 'org_admin'

    def is_admin(self):
        """Returns True for super_admin OR org_admin (used by templates)."""
        return self.role in ('super_admin', 'org_admin', 'admin')


class Pharmacy(db.Model):
    __tablename__ = 'pharmacies'

    id = db.Column(db.Integer, primary_key=True)
    serial_number = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    notification_email = db.Column(db.String(120), nullable=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey('organisations.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    daily_stats = db.relationship('DailyStat', backref='pharmacy', lazy='dynamic')

    def __repr__(self):
        return f'<Pharmacy {self.name}>'


class DailyStat(db.Model):
    __tablename__ = 'daily_stats'

    id = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacies.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    loaded_parcels = db.Column(db.Integer, default=0)
    collected_parcels = db.Column(db.Integer, default=0)
    removed_parcels = db.Column(db.Integer, default=0)
    reminders_sum = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('pharmacy_id', 'date', name='unique_pharmacy_date'),
        db.Index('idx_dailystat_pharmacy_date', 'pharmacy_id', 'date'),
    )

    def __repr__(self):
        return f'<DailyStat {self.pharmacy_id} {self.date}>'


class HourlyDistribution(db.Model):
    __tablename__ = 'hourly_distributions'

    id = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey('pharmacies.id'), nullable=False)
    period = db.Column(db.String(10), nullable=False)
    collected_parcels = db.Column(db.Integer, default=0)
    month = db.Column(db.Date, nullable=False)

    pharmacy = db.relationship('Pharmacy', backref='hourly_distributions')

    __table_args__ = (
        db.UniqueConstraint('pharmacy_id', 'period', 'month', name='unique_pharmacy_period_month'),
        db.Index('idx_hourly_pharmacy_month', 'pharmacy_id', 'month'),
    )

    def __repr__(self):
        return f'<HourlyDistribution {self.pharmacy_id} {self.period} {self.month}>'


class Upload(db.Model):
    __tablename__ = 'uploads'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    records_imported = db.Column(db.Integer, default=0)

    uploader = db.relationship('User', backref='uploads')

    def __repr__(self):
        return f'<Upload {self.filename}>'
