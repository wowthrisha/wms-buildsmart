from contextlib import contextmanager

from flask import Blueprint, request, redirect, jsonify, render_template, abort
from werkzeug.exceptions import HTTPException
from flask_login import login_required, current_user
from sqlalchemy import case, update
from sqlalchemy.orm import joinedload
from app import db
from app.models import Project, ComplianceItem
from app.auth import require_architect
from app.notifications_service import notify_user
from app.time_utils import utc_now

bp = Blueprint('compliance', __name__)


@contextmanager
def _transaction():
    try:
        with db.session.begin_nested():
            yield
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

@bp.route('/compliance')
@login_required
def index():
    require_architect()
    projects = Project.query.options(joinedload(Project.client)).filter_by(architect_id=current_user.id).all()
    return render_template('architect/compliance_standalone.html', projects=projects)

@bp.route('/projects/<int:project_id>/compliance/toggle/<int:item_id>', methods=['POST'])
@login_required
def toggle(project_id, item_id):
    try:
        p = Project.query.get_or_404(project_id)
        if current_user.role == 'architect' and p.architect_id != current_user.id:
            abort(403)
        if current_user.role == 'client' and p.client_id != current_user.id:
            abort(403)
        if current_user.role == 'client' and not p.allow_client_compliance:
            abort(403)

        item = db.session.query(ComplianceItem).filter_by(id=item_id).one_or_none()
        if not item:
            abort(404)
        if item.project_id != p.id:
            abort(404)

        role = current_user.role
        now = utc_now()
        if role == 'architect':
            stmt = update(ComplianceItem).where(
                ComplianceItem.id == item.id,
                ComplianceItem.project_id == p.id,
            ).values(
                arch_checked=case((ComplianceItem.arch_checked.is_(True), False), else_=True),
                arch_checked_at=case((ComplianceItem.arch_checked.is_(True), None), else_=now),
            )
        elif role == 'client':
            stmt = update(ComplianceItem).where(
                ComplianceItem.id == item.id,
                ComplianceItem.project_id == p.id,
            ).values(
                client_checked=case((ComplianceItem.client_checked.is_(True), False), else_=True),
                client_checked_at=case((ComplianceItem.client_checked.is_(True), None), else_=now),
            )
        else:
            abort(403)

        with _transaction():
            db.session.execute(stmt)

        item = ComplianceItem.query.get_or_404(item_id)

        # Notify other party
        partner = p.client if current_user.role == 'architect' else p.architect
        if partner:
            notify_user(partner.id, 'Compliance Updated',
                        f'{current_user.name} toggled compliance item "{item.label}"')

        return jsonify({
            'success': True,
            'item': {
                'id': item.id,
                'arch_checked': bool(item.arch_checked),
                'client_checked': bool(item.client_checked),
                'both_done': bool(item.arch_checked and item.client_checked)
            }
        })
    except HTTPException:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/projects/<int:project_id>/compliance/add-custom', methods=['POST'])
@login_required
def add_custom(project_id):
    require_architect()
    try:
        p = Project.query.get_or_404(project_id)
        if current_user.role == 'architect' and p.architect_id != current_user.id:
            abort(403)
            
        data = request.form
        if request.is_json: data = request.get_json()
        label = (data.get('label') or data.get('name') or '').strip()[:100]
        desc = data.get('description', '').strip()[:500]
        if label:
            with _transaction():
                item = ComplianceItem(project_id=p.id, item=label, label=label, is_custom=True, description=desc)
                db.session.add(item)
                db.session.flush()
            return jsonify({
                'success': True,
                'item': {
                    'id': item.id,
                    'label': item.label or item.item,
                    'description': item.description,
                    'arch_checked': bool(item.arch_checked),
                    'client_checked': bool(item.client_checked)
                }
            })
        return jsonify({'success': False, 'error': 'No label provided'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
