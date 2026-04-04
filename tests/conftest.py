import pytest
import tempfile
import os
from flask import template_rendered
from app import create_app, db
from app.models import User

@pytest.fixture
def app():
    # Setup isolated test file and config
    db_fd, db_path = tempfile.mkstemp()
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + db_path,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret"
    })

    with app.app_context():
        db.create_all()
        yield app

    os.close(db_fd)
    os.unlink(db_path)

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def runner(app):
    return app.test_cli_runner()

@pytest.fixture
def seed_architect_user(app):
    with app.app_context():
        from werkzeug.security import generate_password_hash
        user = User(email='arch@qa.com', name='Test Architect', role='architect',
                    password_hash=generate_password_hash('qapass123', method='pbkdf2:sha256'))
        db.session.add(user)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            user = User.query.filter_by(email='arch@qa.com').first()
        # yield detached copy to avoid thread session issues in test
        return User.query.filter_by(email='arch@qa.com').first()
