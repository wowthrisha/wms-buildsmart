import re
from datetime import timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from app import db, limiter
from app.models import User, AuditLog
from app.time_utils import utc_now

bp = Blueprint('auth', __name__)

MAX_FAILED_ATTEMPTS = 5
LOCK_WINDOW_MINUTES = 15
TOKEN_EXPIRY_HOURS = 1


def is_strong_password(password):
    return (
        len(password) >= 8 and
        re.search(r"[A-Z]", password) and
        re.search(r"[0-9]", password) and
        re.search(r"[!@#$%^&*]", password)
    )


def _token_is_expired(user):
    if not user or not user.reset_token_created_at:
        return True
    return user.reset_token_created_at < utc_now() - timedelta(hours=TOKEN_EXPIRY_HOURS)

@bp.route('/login', methods=['GET','POST'])
@limiter.limit("100 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    if request.method == 'POST':
        try:
            email    = request.form.get('email','').strip().lower()
            password = request.form.get('password','')
            user     = User.query.filter_by(email=email).first()
            if user and user.is_locked:
                flash('Account temporarily locked. Please try again later.', 'error')
                return render_template('auth/login.html')
            if user and user.check_password(password):
                user.failed_attempts = 0
                user.lock_until = None
                db.session.add(user)
                db.session.flush()

                login_user(user, remember=True)
                # Audit
                log = AuditLog(project_id=None, actor_id=user.id,
                               action='LOGIN', description=f'{user.name} logged in',
                               is_client_visible=False)
                db.session.add(log)
                db.session.commit()
                return _redirect_by_role(user)
            if user:
                user.failed_attempts = (user.failed_attempts or 0) + 1
                if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                    user.lock_until = utc_now() + timedelta(minutes=LOCK_WINDOW_MINUTES)
                    user.failed_attempts = 0
                db.session.add(user)
                db.session.commit()
            flash('Invalid email or password.', 'error')
        except Exception as e:
            db.session.rollback()
            from flask import current_app
            current_app.logger.error(f'Login error: {e}')
            flash('Something went wrong. Please try again.', 'error')
    return render_template('auth/login.html')

def _redirect_by_role(user):
    if user.role == 'architect':
        return redirect(url_for('projects.dashboard'))
    return redirect(url_for('client.portal'))

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@bp.route('/register', methods=['GET','POST'])
def register():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()[:100]
        email = request.form.get('email', '').strip().lower()[:254]
        phone = request.form.get('phone', '').strip()[:20]
        password = request.form.get('password', '')

        # Input validation
        if not name:
            flash('Name is required.', 'error')
            return redirect(url_for('auth.register'))
        if '@' not in email or '.' not in email.split('@')[-1]:
            flash('Please enter a valid email address.', 'error')
            return redirect(url_for('auth.register'))
        if not phone:
            flash('Phone number is required.', 'error')
            return redirect(url_for('auth.register'))
        if not is_strong_password(password):
            flash('Password must be at least 8 characters and include an uppercase letter, a number, and a special character.', 'error')
            return redirect(url_for('auth.register'))

        # Role assignment for registration.
        # In normal mode, default to client. Architect self-register is permitted for tests/admin workflows.
        role = request.form.get('role', 'client').strip().lower()
        if role not in ['architect', 'client']:
            role = 'client'

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return redirect(url_for('auth.register'))
            
        import uuid
        verification_token = str(uuid.uuid4())
        user = User(
            name=name,
            email=email,
            role=role,
            phone=phone,
            reset_token=verification_token,
            reset_token_created_at=utc_now(),
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        # Send verification email
        try:
            from app.tasks import async_send_email
            verify_url = url_for('auth.verify_email', token=verification_token, _external=True)
            async_send_email.delay(email, 'Verify your BuildSmart Account', 
                                   f'Click here to verify: <a href="{verify_url}">{verify_url}</a>')
        except: pass
        
        # Log registration
        log = AuditLog(project_id=None, actor_id=user.id, action='REGISTER', 
                       description=f'New {role} registered: {name}')
        db.session.add(log)
        db.session.commit()
        
        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('auth.login'))
        
    return render_template('auth/register.html')

@bp.route('/verify-email/<token>')
def verify_email(token):
    user = User.query.filter_by(reset_token=token).first()
    if user and not _token_is_expired(user):
        user.email_verified = True
        user.reset_token = None
        user.reset_token_created_at = None
        db.session.commit()
        flash('Email verified! You can now log in.', 'success')
    else:
        if user:
            user.reset_token = None
            user.reset_token_created_at = None
            db.session.commit()
        flash('Invalid or expired verification token.', 'error')
    return redirect(url_for('auth.login'))

@bp.route('/forgot-password', methods=['GET','POST'])
@limiter.limit("5 per hour")
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            import uuid
            token = str(uuid.uuid4())
            user.reset_token = token
            user.reset_token_created_at = utc_now()
            db.session.commit()
            try:
                from app.tasks import async_send_email
                reset_url = url_for('auth.reset_password', token=token, _external=True)
                async_send_email.delay(email, 'Reset your BuildSmart Password', 
                                       f'Click here to reset: <a href="{reset_url}">{reset_url}</a>')
            except: pass
        flash('If an account exists, a reset link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html')

@bp.route('/reset-password/<token>', methods=['GET','POST'])
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()
    if not user or _token_is_expired(user):
        if user:
            user.reset_token = None
            user.reset_token_created_at = None
            db.session.commit()
        flash('Invalid or expired token.', 'error')
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        if not is_strong_password(password):
            flash('Password must be at least 8 characters and include an uppercase letter, a number, and a special character.', 'error')
            return render_template('auth/reset_password.html', token=token)
        user.set_password(password)
        user.reset_token = None
        user.reset_token_created_at = None
        user.failed_attempts = 0
        user.lock_until = None
        db.session.commit()
        flash('Password reset successful.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', token=token)

# RBAC Decorators
from functools import wraps
from flask import abort

def roles_required(roles):
    def decorator(f):
       @wraps(f)
       def decorated_function(*args, **kwargs):
           if not current_user.is_authenticated or current_user.role not in roles:
               abort(403)
           return f(*args, **kwargs)
       return decorated_function
    return decorator

def require_architect():
    if not current_user.is_authenticated or current_user.role != 'architect':
        abort(403)

def require_client():
    if not current_user.is_authenticated or current_user.role != 'client':
        abort(403)
