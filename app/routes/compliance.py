from contextlib import contextmanager

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.auth import require_architect
from app.compliance_catalog import normalize_doc_type
from app.compliance_service import build_compliance_tree, compliance_summary, coverage_by_category, ensure_project_compliance_items
from app.models import ComplianceItem, Project
from app.notifications_service import notify_user
from app.services.document_service import COMPLIANCE_DOC_DISPLAY_NAMES, create_or_replace_document, verify_document
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


def _load_project(project_id: int) -> Project:
    project = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and project.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client' and project.client_id != current_user.id:
        abort(403)
    return project


def _item_payload(item: ComplianceItem) -> dict:
    extracted_fields = {}
    if item.document and item.document.extracted_data:
        try:
            import json
            extracted_fields = json.loads(item.document.extracted_data)
        except Exception:
            extracted_fields = {}

    return {
        'id': item.id,
        'doc_type': item.doc_type or item.item,
        'label': item.custom_label or item.label or item.item,
        'category': item.category or 'custom',
        'required': bool(item.required),
        'status': item.status or 'missing',
        'status_label': item.display_status,
        'status_tone': item.status_tone,
        'document_id': item.document_id,
        'description': item.description or '',
        'importance': item.importance_label,
        'extracted_fields': extracted_fields,
        'arch_checked': bool(item.arch_checked),
        'client_checked': bool(item.client_checked),
        'is_custom': bool(item.is_custom),
        'pending_verification': bool(item.document.pending_verification) if item.document else False,
        'updated_at': item.updated_at.isoformat() if item.updated_at else None,
    }


# ─── Overview ──────────────────────────────────────────────────────────────

@bp.route('/compliance')
@login_required
def index():
    require_architect()
    projects = Project.query.filter_by(architect_id=current_user.id).all()
    rows = []
    for project in projects:
        summary = compliance_summary(project)
        rows.append({'project': project, 'summary': summary})
    db.session.commit()
    return render_template('architect/compliance_standalone.html', projects=rows)


@bp.route('/projects/<int:project_id>/compliance/state')
@login_required
def state(project_id):
    project = _load_project(project_id)
    items = ensure_project_compliance_items(project.id)
    db.session.commit()
    return jsonify({
        'success': True,
        'project_id': project.id,
        'summary': compliance_summary(project),
        'tree': build_compliance_tree(project),
        'coverage': coverage_by_category(project),
        'items': [_item_payload(item) for item in items],
    })


# ─── Single-step upload ────────────────────────────────────────────────────
# Replaces the old two-step (upload → attach-document) flow.
# Both architect and client can call this.

@bp.route('/projects/<int:project_id>/compliance/<int:item_id>/upload', methods=['POST'])
@login_required
def upload(project_id, item_id):
    project = _load_project(project_id)
    item = ComplianceItem.query.filter_by(id=item_id, project_id=project.id).first_or_404()

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'No file provided.'}), 400

    display_name = COMPLIANCE_DOC_DISPLAY_NAMES.get(
        item.doc_type or '',
        item.custom_label or item.label or item.doc_type or 'Document',
    )

    try:
        doc, _ = create_or_replace_document(
            file=file,
            project_id=project.id,
            uploaded_by=current_user.id,
            uploaded_by_role=current_user.role,
            source_module='compliance',
            display_name=display_name,
            compliance_doc_type=item.doc_type,
            visible_to_client=(current_user.role == 'client'),
            existing_document_id=item.document_id if item.document_id else None,
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    with _transaction():
        item.document_id = doc.id
        item.status = 'client_uploaded' if current_user.role == 'client' else 'uploaded'
        item.arch_checked = False
        item.updated_at = utc_now()

    # Notify partner
    partner = project.client if current_user.role == 'architect' else project.architect
    if partner:
        try:
            notify_user(
                partner.id,
                'Compliance Updated',
                f'{display_name} was {"uploaded by client — please verify" if current_user.role == "client" else "uploaded"} in {project.name}.',
            )
        except Exception:
            pass

    return jsonify({'success': True, 'item': _item_payload(item), 'document_id': doc.id})


# ─── Verify (architect marks status=verified, clears pending flag) ─────────

@bp.route('/projects/<int:project_id>/compliance/verify/<int:item_id>', methods=['POST'])
@login_required
def verify(project_id, item_id):
    require_architect()
    project = _load_project(project_id)
    item = ComplianceItem.query.filter_by(id=item_id, project_id=project.id).first_or_404()

    if not item.document_id:
        return jsonify({'success': False, 'error': 'Upload a document before verifying.'}), 400

    # Toggle verified ↔ uploaded
    new_status = (
        'client_uploaded'
        if item.status == 'verified' and item.document and item.document.uploader_role == 'client'
        else 'uploaded' if item.status == 'verified'
        else 'verified'
    )

    with _transaction():
        item.status = new_status
        item.arch_checked = (new_status == 'verified')
        item.checked_at = utc_now() if item.arch_checked else None
        item.updated_at = utc_now()

    # Also clear pending_verification on the linked document
    if item.document and new_status == 'verified':
        try:
            verify_document(item.document_id, current_user.id)
        except Exception:
            pass

    # Notify client
    if project.client:
        try:
            lbl = item.custom_label or item.label or item.doc_type
            msg = f'{lbl} has been verified.' if new_status == 'verified' else f'{lbl} verification was removed.'
            notify_user(project.client.id, 'Compliance Verified', msg)
        except Exception:
            pass

    return jsonify({'success': True, 'item': _item_payload(item)})


# ─── Toggle checkbox (dual-role) ───────────────────────────────────────────

@bp.route('/projects/<int:project_id>/compliance/toggle/<int:item_id>', methods=['POST'])
@login_required
def toggle(project_id, item_id):
    project = _load_project(project_id)
    item = ComplianceItem.query.filter_by(id=item_id, project_id=project.id).first_or_404()

    if current_user.role == 'architect':
        if not item.document_id:
            return jsonify({
                'success': False,
                'error': 'Cannot verify — no document is attached to this item.',
            }), 400
        with _transaction():
            item.status = (
                'client_uploaded'
                if item.status == 'verified' and item.document and item.document.uploader_role == 'client'
                else 'uploaded' if item.status == 'verified'
                else 'verified'
            )
            item.arch_checked = item.status == 'verified'
            item.checked_at = utc_now() if item.arch_checked else None
            item.updated_at = utc_now()
    else:
        if not project.allow_client_compliance:
            abort(403)
        with _transaction():
            item.client_checked = not bool(item.client_checked)
            item.client_checked_at = utc_now() if item.client_checked else None
            item.updated_at = utc_now()

    return jsonify({
        'success': True,
        'item': _item_payload(item),
        'arch_checked': bool(item.arch_checked),
        'client_checked': bool(item.client_checked),
    })


# ─── Add custom compliance item (architect only) ───────────────────────────

@bp.route('/projects/<int:project_id>/compliance/add-custom', methods=['POST'])
@login_required
def add_custom(project_id):
    require_architect()
    project = _load_project(project_id)
    data = request.get_json(silent=True) or request.form
    label = (data.get('label') or data.get('name') or '').strip()[:200]
    description = (data.get('description') or '').strip()[:500]
    category = (data.get('category') or 'Other').strip()[:50]
    required_raw = (data.get('required') or '').strip().lower() if hasattr(data, 'get') else ''
    required = required_raw not in {'false', '0', 'optional', 'no'}

    if not label:
        return jsonify({'success': False, 'error': 'No label provided'})

    doc_type = f'custom_{normalize_doc_type(label)}_{int(utc_now().timestamp())}'

    with _transaction():
        item = ComplianceItem(
            project_id=project.id,
            item=doc_type,
            doc_type=doc_type,
            label=label,
            custom_label=label,
            category=category,
            required=required,
            status='missing' if required else 'optional',
            is_custom=True,
            description=description,
            added_by=current_user.id,
            added_at=utc_now(),
            updated_at=utc_now(),
        )
        db.session.add(item)
        db.session.flush()

    return jsonify({'success': True, 'item': _item_payload(item)})


# ─── Remove custom compliance item (architect only) ────────────────────────

@bp.route('/projects/<int:project_id>/compliance/remove-custom/<int:item_id>', methods=['POST'])
@login_required
def remove_custom(project_id, item_id):
    require_architect()
    project = _load_project(project_id)
    item = ComplianceItem.query.filter_by(id=item_id, project_id=project.id, is_custom=True).first_or_404()
    db.session.delete(item)
    db.session.commit()
    return jsonify({'success': True})
