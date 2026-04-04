from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from config import Config
from app.time_utils import ensure_utc, utc_now
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.orm import joinedload

db     = SQLAlchemy()
login  = LoginManager()
csrf   = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://", strategy="fixed-window")

# Helper function for notifications
def _notify(user_id, title, body):
    from app.models import Notification
    n = Notification(user_id=user_id, title=title, body=body)
    db.session.add(n)
    db.session.commit()
    
    # Real-time trigger
    try:
        from app.routes.notifications import send_live_notif
        send_live_notif(user_id, title, body)
    except: pass

def seed_db(app):
    from app.models import User, Project, ComplianceItem
    from werkzeug.security import generate_password_hash
    from sqlalchemy.exc import IntegrityError
    
    with app.app_context():
        try:
            # Architect
            arch = User.query.filter_by(email='arch@qa.com').first()
            if not arch:
                arch = User(name='QA Architect', email='arch@qa.com', role='architect',
                            password_hash=generate_password_hash('qapass123'))
                db.session.add(arch)
                db.session.commit()
                
            # Client
            client = User.query.filter_by(email='client@qa.com').first()
            if not client:
                client = User(name='QA Client', email='client@qa.com', role='client',
                             phone='+916369727809',
                             password_hash=generate_password_hash('qapass123'))
                db.session.add(client)
                db.session.commit()
            elif not client.phone:
                client.phone = '+916369727809'
                db.session.commit()
                
            # Demo Project
            if not Project.query.filter_by(name='Demo Project').first():
                p = Project(name='Demo Project', architect_id=arch.id, client_id=client.id, 
                            status='Design', plot_zone='Mixed')
                db.session.add(p)
                db.session.commit()
                
                # Default Compliance
                for label in ['Building Plan Approval', 'Fire NOC']:
                    db.session.add(ComplianceItem(project_id=p.id, label=label))
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            raise e

def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    # Debug: Print DB path
    print(f"DATABASE_URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")
    login.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Security: Enforce production-grade cookie settings
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    if app.config.get('FLASK_ENV') == 'production':
        app.config['SESSION_COOKIE_SECURE'] = True

    login.login_view     = 'auth.login'
    login.login_message  = 'Please log in to access this page.'

    @login.user_loader
    def load_user(user_id):
        from app.models import User
        return db.session.get(User, int(user_id))

    # Dev mode: role override for testing (DEVELOPMENT ONLY)
    @app.before_request
    def apply_dev_role_override():
        from flask_login import current_user
        if current_user.is_authenticated:
            from flask import session
            override_role = session.get('_dev_role_override')
            if override_role:
                # Temporarily override the role (in-memory only)
                current_user._dev_role_override = override_role

    # Context processor to expose dev role override to templates
    @app.context_processor
    def inject_dev_context():
        from flask_login import current_user
        dev_role = None
        if current_user.is_authenticated:
            dev_role = getattr(current_user, '_dev_role_override', None)
        return {'dev_role_override': dev_role}

    # Jinja2 filters
    @app.template_filter('timeago')
    def timeago_filter(dt):
        if dt is None: return ''
        now = ensure_utc(utc_now())
        current = ensure_utc(dt)
        diff = now - current
        s    = int(diff.total_seconds())
        if s < 60:   return f'{s}s ago'
        if s < 3600: return f'{s//60}m ago'
        if s < 86400:return f'{s//3600}h ago'
        return dt.strftime('%d %b %Y')

    @app.template_filter('inr')
    def inr_filter(val):
        return f'₹{val:,.0f}' if val else '₹0'

    # Context processor — inject into all templates
    # Global filters
    import json
    @app.template_filter('fromjson')
    def fromjson_filter(s):
        return json.loads(s) if s else {}

    @app.template_filter('strftime')
    def strftime_filter(value, format='%d %b %Y'):
        if not value or not hasattr(value, 'strftime'): return ""
        return value.strftime(format)

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        from app.models import Project, Notification, User, AuditLog
        data = {'all_projects': [], 'pending_count': 0, 'notifications': [],
                'unread_notifs': 0, 'all_clients': [], 'active_project': None}
        if current_user.is_authenticated:
            # Notifications for everyone
            notifs = Notification.query.filter_by(
                user_id=current_user.id).order_by(Notification.created_at.desc()).limit(10).all()
            data['notifications'] = notifs
            data['unread_notifs'] = sum(1 for n in notifs if not n.read)

            if current_user.role == 'architect':
                data['all_projects'] = Project.query.filter_by(
                    architect_id=current_user.id).options(joinedload(Project.client)).order_by(Project.updated_at.desc()).all()
                data['all_clients'] = User.query.filter_by(role='client').all() # All clients for project creation
            elif current_user.role == 'client':
                from app.routes.client import get_client_project
                data['projects'] = Project.query.filter_by(client_id=current_user.id).options(joinedload(Project.architect)).all()
                data['project'] = get_client_project()
                
        data['AuditLog'] = AuditLog
        data['Notification'] = Notification
        return data

    # Register blueprints
    from app.auth     import bp as auth_bp
    from app.routes.projects   import bp as proj_bp
    from app.routes.plot       import bp as plot_bp
    from app.routes.documents  import bp as docs_bp
    from app.routes.compliance import bp as comp_bp
    from app.routes.meetings   import bp as meet_bp
    from app.routes.payments   import bp as pay_bp
    from app.routes.activity   import bp as act_bp
    from app.routes.client     import bp as client_bp
    from app.routes.settings   import bp as settings_api
    from app.routes.notifications import bp as notif_api
    from app.routes.dev          import bp as dev_bp
    from app.routes.requirements import bp as req_bp

    for bp in [auth_bp, proj_bp, plot_bp, docs_bp, comp_bp, meet_bp, pay_bp, act_bp, client_bp, settings_api, notif_api, dev_bp, req_bp]:
        app.register_blueprint(bp)

    # P2-15: Custom error pages
    from flask import render_template as _rt
    @app.errorhandler(404)
    def not_found(e):
        return _rt('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        return _rt('errors/403.html'), 403

    @app.errorhandler(500)
    def server_error(e):
        return _rt('errors/500.html'), 500

    @app.cli.command("seed")
    def seed_command():
        """Seed the database with QA defaults."""
        try:
            seed_db(app)
            print("Database seamlessly seeded!")
        except Exception as e:
            print(f"FAILED to seed database: {e}")

    with app.app_context():
        db.create_all()
        _apply_migrations()

    return app


def _apply_migrations():
    """Apply ALTER TABLE migrations for columns added after initial db.create_all().
    Each statement is attempted individually; failures are silently ignored because
    SQLite raises an error if a column already exists."""
    migrations = [
        "ALTER TABLE document ADD COLUMN visible_to_client BOOLEAN DEFAULT 0",
        "ALTER TABLE document ADD COLUMN meeting_id INTEGER",
        "ALTER TABLE document ADD COLUMN requirement_id INTEGER",
        "ALTER TABLE document_version ADD COLUMN file_type VARCHAR(100)",
        "ALTER TABLE document_version ADD COLUMN file_size VARCHAR(50)",
        "ALTER TABLE document_version ADD COLUMN version_label VARCHAR(100)",
        "ALTER TABLE user ADD COLUMN phone VARCHAR(20)",
        "ALTER TABLE user ADD COLUMN reset_token_created_at DATETIME",
        "ALTER TABLE user ADD COLUMN failed_attempts INTEGER DEFAULT 0",
        "ALTER TABLE user ADD COLUMN lock_until DATETIME",
        "ALTER TABLE meeting ADD COLUMN description TEXT",
        "ALTER TABLE meeting ADD COLUMN outcome VARCHAR(255)",
        "ALTER TABLE meeting ADD COLUMN completed_at DATETIME",
        "ALTER TABLE project_image ADD COLUMN meeting_id INTEGER",
        "ALTER TABLE project_image ADD COLUMN requirement_id INTEGER",
        "ALTER TABLE comment ADD COLUMN parent_id INTEGER",
        # MeetingLog table is created by db.create_all() on first run.
        # These ALTER statements handle columns added to existing tables only.
    ]
    with db.engine.raw_connection() as conn:
        cursor = conn.cursor()
        for sql in migrations:
            try:
                cursor.execute(sql)
                conn.commit()
            except Exception:
                pass  # column already exists — safe to ignore
