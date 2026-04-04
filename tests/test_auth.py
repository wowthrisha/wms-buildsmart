import pytest
from datetime import datetime, timedelta, timezone

from app import db
from app.models import User

def test_architect_login_success(client, seed_architect_user):
    """Test successful architect login and session management."""
    # Ensure login page renders first
    res = client.get('/login')
    assert res.status_code == 200

    response = client.post('/login', data={
        'email': seed_architect_user.email,
        'password': 'qapass123'
    }, follow_redirects=True)
    
    assert response.status_code == 200
    # Validates successful redirect to active dashboard
    assert b"Dashboard" in response.data or b"Projects" in response.data

def test_login_invalid_credentials(client, seed_architect_user):
    """Test failed login protects against brute force and shows errors."""
    response = client.post('/login', data={
        'email': seed_architect_user.email,
        'password': 'wrongpassword'
    }, follow_redirects=True)
    
    # Should stay on the login page
    assert b"Invalid email or password" in response.data
    assert response.status_code == 200


def test_register_rejects_weak_password(client):
    response = client.post('/register', data={
        'name': 'Weak User',
        'email': 'weak@example.com',
        'phone': '+911234567890',
        'password': 'weakpass',
        'role': 'client',
    }, follow_redirects=True)

    assert b'Password must be at least 8 characters' in response.data


def test_reset_token_expiry_blocks_reset(client, app):
    with app.app_context():
        user = User(name='Reset User', email='reset@example.com', role='client', phone='+911111111111')
        user.set_password('Strong1!')
        user.reset_token = 'expired-token'
        user.reset_token_created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.session.add(user)
        db.session.commit()

    response = client.get('/reset-password/expired-token', follow_redirects=True)
    assert b'Invalid or expired token' in response.data


def test_login_lockout_after_repeated_failures(client, app):
    with app.app_context():
        user = User(name='Locked User', email='locked@example.com', role='architect', phone='+919999999999')
        user.set_password('Strong1!')
        db.session.add(user)
        db.session.commit()

    for _ in range(5):
        client.post('/login', data={'email': 'locked@example.com', 'password': 'wrong'}, follow_redirects=True)

    response = client.post('/login', data={'email': 'locked@example.com', 'password': 'Strong1!'}, follow_redirects=True)
    assert b'Account temporarily locked' in response.data
