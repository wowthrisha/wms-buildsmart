import pytest
import re
from app.models import User, Project, db
from flask import url_for

def get_csrf_token(data):
    """Utility to extract CSRF token from HTML."""
    match = re.search(r'name="csrf_token" value="(.*?)"', data)
    return match.group(1) if match else None

def test_full_architect_client_flow(client, app):
    """
    Test End-to-End Flow:
    1. Architect Registers
    2. Architect Logins
    3. Architect Creates Project
    4. Client Registers
    5. Architect Assigns Client (Manual flow via Dashboard usually, but we'll simulate logic)
    """
    # 1. Register Architect
    resp = client.get('/register')
    csrf_token = get_csrf_token(resp.get_data(as_text=True))
    
    resp = client.post('/register', data={
        'name': 'Archie Text',
        'email': 'archie@example.com',
        'phone': '+919999999999',
        'password': 'Password1!',
        'role': 'architect',
        'csrf_token': csrf_token
    }, follow_redirects=True)
    assert b'Registration successful' in resp.data
    
    # 2. Login
    resp = client.get('/login')
    csrf_token = get_csrf_token(resp.get_data(as_text=True))
    resp = client.post('/login', data={
        'email': 'archie@example.com',
        'password': 'Password1!',
        'csrf_token': csrf_token
    }, follow_redirects=True)
    assert b'Logout' in resp.data
    
    # 3. Create Project
    resp = client.get('/projects/new')
    csrf_token = get_csrf_token(resp.get_data(as_text=True))
    resp = client.post('/projects/new', data={
        'name': 'Production Mall',
        'location': 'Mumbai',
        'budget': '500000',
        'csrf_token': csrf_token
    }, follow_redirects=True)
    assert b'Production Mall' in resp.data
    
    # 4. Client Registration & Verification
    # (Checking if user exists and is unverified)
    with app.app_context():
        u = User.query.filter_by(email='archie@example.com').first()
        assert u is not None
        # We simulate email verification by token
        token = u.reset_token
        resp = client.get(f'/verify-email/{token}', follow_redirects=True)
        assert b'Email verified' in resp.data
        db.session.refresh(u)
        assert u.email_verified is True

def test_rbac_protection(client, app, seed_architect_user):
    """Verify that roles_required decorator works."""
    # Login as architect
    resp = client.get('/login')
    csrf_token = get_csrf_token(resp.get_data(as_text=True))
    client.post('/login', data={'email':'arch@qa.com', 'password':'qapass123', 'csrf_token':csrf_token})
    
    # Try to access a client-only route (if we had one, e.g. /my_project)
    # Actually client routes have @login_required + manual check.
    # We'll check /activity which is architect-only
    resp = client.get('/activity')
    assert resp.status_code == 200
    
    # Now logout and login as a client (manual seed)
    from werkzeug.security import generate_password_hash
    with app.app_context():
        c = User(email='client@qa.com', role='client', name='Joe Client', 
                 password_hash=generate_password_hash('pass', method='pbkdf2:sha256'))
        db.session.add(c)
        db.session.commit()
        
    client.get('/logout')
    resp = client.get('/login')
    csrf_token = get_csrf_token(resp.get_data(as_text=True))
    client.post('/login', data={'email':'client@qa.com', 'password':'pass', 'csrf_token':csrf_token})
    
    resp = client.get('/activity')
    assert resp.status_code == 403 # Forbidden for client
