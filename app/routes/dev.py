"""
Development & Testing Routes
Only available when FLASK_DEBUG=true
"""
from flask import Blueprint, redirect, request, session, url_for, g
from flask_login import login_required, current_user
import os

bp = Blueprint('dev', __name__)

@bp.before_request
def check_debug():
    """Ensure these routes only work in debug mode"""
    if not os.environ.get('FLASK_DEBUG', 'False').lower() in ['true', '1']:
        return redirect(url_for('projects.root'))

@bp.route('/dev/switch-role')
@login_required
def switch_role():
    """
    Temporarily switch role for testing dual-user flows (TESTING ONLY).
    Stores override in session; provides visual test harness.
    Note: For true dual-session testing, use incognito/private browser window.
    """
    override = session.get('_dev_role_override')
    original = session.get('_dev_original_role', current_user.role)
    
    if not override:
        # Store original role on first switch
        session['_dev_original_role'] = current_user.role
        # Switch to opposite role
        session['_dev_role_override'] = 'client' if current_user.role == 'architect' else 'architect'
    else:
        # Toggle override off
        session['_dev_role_override'] = None
    
    session.modified = True
    return redirect(request.referrer or url_for('projects.root'))

@bp.route('/dev/reset-role')
@login_required
def reset_role():
    """Reset role back to original (clear dev mode)"""
    session.pop('_dev_role_override', None)
    session.pop('_dev_original_role', None)
    session.modified = True
    return redirect(request.referrer or url_for('projects.root'))
