from flask import Blueprint, request, redirect, render_template, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import User, Notification

bp = Blueprint('settings', __name__)


def _is_checked(*field_names):
    return any(field_name in request.form for field_name in field_names)

@bp.route('/settings', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        try:
            current_user.reminders_email = _is_checked('email', 'reminders_email')
            current_user.reminders_sms = _is_checked('sms', 'reminders_sms')
            current_user.name = request.form.get('name', current_user.name).strip()[:120]
            current_user.phone = request.form.get('phone', '').strip()[:20]
            if current_user.role == 'client':
                profession = request.form.get('profession', '').strip()[:120]
                current_user.profession = profession or None
            db.session.commit()
            from flask import flash, url_for
            flash('Settings saved.', 'success')
            return redirect(url_for('settings.index'))
        except Exception as e:
            db.session.rollback()
            from flask import flash, current_app, url_for
            current_app.logger.error(f'Error in {request.endpoint}: {e}')
            flash('Something went wrong. Please try again.', 'error')
            return redirect(url_for('settings.index'))
        
    if current_user.role == 'architect':
        return render_template('architect/settings.html')
    return render_template('client/settings.html')

@bp.route('/notifications/read-all', methods=['POST'])
@login_required
def read_all():
    Notification.query.filter_by(user_id=current_user.id).update({'read': True})
    db.session.commit()
    return redirect(request.referrer or '/dashboard')

@bp.route('/notifications/<int:notif_id>/read', methods=['POST'])
@login_required
def read_one(notif_id):
    n = Notification.query.get_or_404(notif_id)
    if n.user_id == current_user.id:
        n.read = True
        db.session.commit()
    return redirect(request.referrer or '/dashboard')
