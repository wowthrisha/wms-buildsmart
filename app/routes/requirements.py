from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.auth import roles_required
from app.models import (
    AuditLog,
    Comment,
    Meeting,
    MeetingLog,
    Project,
    Requirement,
    RequirementComment,
)
from app.notifications_service import notify_user

bp = Blueprint('requirements', __name__)

KANBAN_STATES = ['new', 'confirmed', 'in_progress', 'done']
KANBAN_LABELS = {
    'new': 'New',
    'confirmed': 'Confirmed',
    'in_progress': 'In Progress',
    'done': 'Done',
}


def _project_access_check(project):
    if current_user.role == 'architect' and project.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client' and project.client_id != current_user.id:
        abort(403)


def _project_or_403(project_id):
    project = Project.query.get_or_404(project_id)
    _project_access_check(project)
    return project


def _get_meeting_or_403(project_id, meeting_id):
    meeting = Meeting.query.filter_by(id=meeting_id, project_id=project_id).first_or_404()
    _project_access_check(meeting.project)
    return meeting, meeting.project


def _get_requirement_or_403(project_id, req_id):
    project = _project_or_403(project_id)
    requirement = Requirement.query.filter_by(id=req_id, project_id=project_id).first_or_404()
    return requirement, project


def _notify_other_party(project, title, body):
    try:
        other_id = project.client_id if current_user.role == 'architect' else project.architect_id
        if other_id:
            notify_user(other_id, title, body)
    except Exception:
        pass


def _meeting_redirect(meeting_id=None, fallback_project_id=None):
    target = request.form.get('next') or request.args.get('next')
    meeting = None
    if meeting_id:
        if fallback_project_id:
            meeting = Meeting.query.filter_by(id=meeting_id, project_id=fallback_project_id).first()
        else:
            meeting = Meeting.query.get(meeting_id)

    if target == 'mom':
        if meeting:
            return redirect(url_for('meetings.project_mom', project_id=meeting.project_id, meeting_id=meeting.id))
        if fallback_project_id:
            return redirect(url_for('meetings.project_meetings', project_id=fallback_project_id))
        return redirect(url_for('meetings.index'))

    if meeting:
        return redirect(url_for('meetings.project_detail', project_id=meeting.project_id, meeting_id=meeting.id))
    if fallback_project_id:
        return redirect(url_for('requirements.kanban', project_id=fallback_project_id))
    return redirect(url_for('meetings.index'))


def _log_meeting_event(meeting_id, action, note=''):
    if not meeting_id:
        return
    try:
        db.session.add(MeetingLog(
            meeting_id=meeting_id,
            actor_id=current_user.id,
            action=action,
            note=note,
        ))
    except Exception:
        pass


def _build_columns(project_id):
    requirements = Requirement.query.filter_by(project_id=project_id).order_by(Requirement.created_at.desc()).all()
    columns = {state: [] for state in KANBAN_STATES}
    for requirement in requirements:
        columns.setdefault(requirement.status, []).append(requirement)
    return columns


def _build_columns_json(project_id):
    columns = _build_columns(project_id)
    result = {}
    for state, requirements in columns.items():
        result[state] = [{
            'id': requirement.id,
            'title': requirement.title,
            'category': requirement.category,
            'description': requirement.description,
            'created_at': requirement.created_at.strftime('%d %b %Y') if requirement.created_at else '',
            'created_by': requirement.creator.name if requirement.creator else '—',
            'created_by_role': requirement.creator.role.title() if requirement.creator else 'Unknown',
            'meeting_id': requirement.meeting_id,
        } for requirement in requirements]
    return result


@bp.route('/projects/<int:project_id>/meetings/<int:meeting_id>/comment', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def add_comment(project_id, meeting_id):
    meeting, project = _get_meeting_or_403(project_id, meeting_id)

    content = (request.form.get('content') or '').strip()
    if not content:
        flash('Comment cannot be empty.', 'error')
        return _meeting_redirect(meeting.id, project.id)

    parent_id = request.form.get('parent_id')
    parent = None
    if parent_id:
        parent = Comment.query.get_or_404(int(parent_id))
        if parent.meeting_id != meeting.id:
            abort(403)

    comment = Comment(
        meeting_id=meeting.id,
        user_id=current_user.id,
        parent_id=parent.id if parent else None,
        content=content[:2000],
    )
    db.session.add(comment)
    db.session.add(AuditLog(
        project_id=project.id,
        actor_id=current_user.id,
        action='COMMENT',
        description=f'Added comment to meeting #{meeting.id}',
        is_client_visible=True,
    ))
    _log_meeting_event(meeting.id, 'COMMENT_ADDED', content[:120])
    db.session.commit()

    _notify_other_party(project, 'New meeting comment', f'{current_user.name} added a comment on meeting #{meeting.id}.')

    flash('Comment added.', 'success')
    return _meeting_redirect(meeting.id, project.id)


@bp.route('/projects/<int:project_id>/comments/<int:comment_id>/convert', methods=['POST'])
@login_required
def convert_comment(project_id, comment_id):
    project = _project_or_403(project_id)
    comment = Comment.query.get_or_404(comment_id)
    meeting = comment.meeting
    if not meeting or meeting.project_id != project_id:
        abort(403)

    if comment.requirement_id:
        flash('That comment is already linked to a requirement.', 'info')
        return _meeting_redirect(meeting.id, project.id)

    title = (request.form.get('req_title') or comment.content[:80] or 'Client Requirement').strip()[:255]
    category = (request.form.get('req_category') or 'General').strip()[:100]

    requirement = Requirement(
        project_id=project.id,
        meeting_id=meeting.id,
        title=title,
        description=comment.content,
        category=category,
        status='new',
        source=current_user.role,
        raised_by=current_user.id,
        created_by=current_user.id,
    )
    db.session.add(requirement)
    db.session.flush()

    comment.requirement_id = requirement.id
    db.session.add(AuditLog(
        project_id=project.id,
        actor_id=current_user.id,
        action='REQ_CREATE',
        description=f'Converted comment into requirement "{title}"',
        is_client_visible=True,
    ))
    _log_meeting_event(meeting.id, 'REQUIREMENT_CREATED', title)
    db.session.commit()

    _notify_other_party(project, 'Requirement created', f'{current_user.name} created requirement "{title}" from meeting #{meeting.id}.')

    flash(f'Requirement "{title}" created.', 'success')
    return _meeting_redirect(meeting.id, project.id)


@bp.route('/projects/<int:project_id>/requirements/add', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def add_requirement(project_id):
    project = _project_or_403(project_id)

    meeting_id = request.form.get('meeting_id', type=int)
    description = (request.form.get('description') or '').strip()
    source = (request.form.get('source') or '').strip()
    title = (request.form.get('title') or '').strip()
    if not title and source == 'meeting_change' and current_user.role == 'client' and description:
        title = 'Client Requirement'
    title = title[:255]

    if not title:
        flash('Title required.', 'error')
        if meeting_id:
            return _meeting_redirect(meeting_id, project.id)
        return redirect(url_for('requirements.kanban', project_id=project.id))

    meeting = None
    if meeting_id:
        meeting = Meeting.query.filter_by(id=meeting_id, project_id=project.id).first_or_404()

    initial_status = (request.form.get('status') or '').strip() if current_user.role == 'architect' else 'new'
    if initial_status not in KANBAN_STATES:
        initial_status = 'confirmed' if current_user.role == 'architect' else 'new'

    requirement = Requirement(
        project_id=project.id,
        meeting_id=meeting.id if meeting else None,
        title=title,
        description=description[:4000],
        category=(request.form.get('category') or 'General').strip()[:100],
        status=initial_status,
        source=current_user.role,
        raised_by=current_user.id,
        created_by=current_user.id,
    )
    db.session.add(requirement)
    db.session.flush()

    db.session.add(AuditLog(
        project_id=project.id,
        actor_id=current_user.id,
        action='REQ_CREATE',
        description=f'Created requirement "{title}"',
        is_client_visible=True,
    ))
    _log_meeting_event(meeting.id if meeting else None, 'REQUIREMENT_CREATED', title)
    db.session.commit()

    _notify_other_party(project, 'New requirement', f'{current_user.name} created requirement "{title}".')

    flash('Requirement added.', 'success')
    if meeting:
        return _meeting_redirect(meeting.id, project.id)
    return redirect(url_for('requirements.kanban', project_id=project.id))


@bp.route('/projects/<int:project_id>/requirements/suggested', methods=['GET'])
@login_required
def suggested_requirements(project_id):
    """Returns AI-suggested requirements from RequirementCard pipeline output."""
    project = _project_or_403(project_id)
    from app.models import RequirementCard, VisualReference
    cards = (RequirementCard.query
             .join(VisualReference, RequirementCard.visual_reference_id == VisualReference.id)
             .filter(VisualReference.project_id == project_id)
             .filter(RequirementCard.fused_label != None)
             .filter(RequirementCard.accepted != True)
             .all())
    return jsonify([{
        'id': c.id,
        'fused_label': c.fused_label,
        'vision_tags': c.vision_tags_json,
        'extracted_intent': c.extracted_intent,
        'divergence_score': c.divergence_score,
        'image_url': url_for('documents.serve_file', filename=c.visual_reference.filename)
                    if c.visual_reference else None,
    } for c in cards])


@bp.route('/projects/<int:project_id>/requirements/accept-suggestion/<int:card_id>', methods=['POST'])
@login_required
def accept_suggestion(project_id, card_id):
    """Architect accepts an AI suggestion, creating a Requirement from it."""
    if current_user.role != 'architect':
        return jsonify({'error': 'Forbidden'}), 403
    project = _project_or_403(project_id)
    from app.models import RequirementCard, VisualReference
    card = RequirementCard.query.get_or_404(card_id)
    if card.visual_reference and card.visual_reference.project_id != project_id:
        abort(403)
    req = Requirement(
        project_id=project_id,
        title=card.fused_label,
        description=card.extracted_intent or '',
        category='interior',
        status='confirmed',
        source='ai_suggestion',
        raised_by=current_user.id,
        created_by=current_user.id,
    )
    db.session.add(req)
    card.accepted = True
    db.session.commit()
    return jsonify({'success': True, 'requirement_id': req.id, 'title': req.title})


@bp.route('/requirements/<int:req_id>', methods=['PATCH'])
@login_required
def update_requirement(req_id):
    """Edit requirement title/description/category."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'No data'}), 400
    req = Requirement.query.get_or_404(req_id)
    project = Project.query.get_or_404(req.project_id)
    if current_user.role == 'architect':
        if project.architect_id != current_user.id:
            abort(403)
    elif current_user.role == 'client':
        if project.client_id != current_user.id:
            abort(403)
        if req.raised_by != current_user.id:
            abort(403)
    if 'title' in data and data['title'].strip():
        req.title = data['title'].strip()
    if 'description' in data:
        req.description = data['description']
    if 'category' in data:
        req.category = data['category']
    req.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'id': req.id, 'title': req.title})


@bp.route('/requirements/<int:req_id>', methods=['DELETE'])
@login_required
def delete_requirement_global(req_id):
    """Delete a requirement — architect only. Called from kanban JS."""
    if current_user.role != 'architect':
        return jsonify({'error': 'Forbidden'}), 403
    req = Requirement.query.get_or_404(req_id)
    project = Project.query.get_or_404(req.project_id)
    if project.architect_id != current_user.id:
        abort(403)
    db.session.add(AuditLog(
        project_id=req.project_id,
        actor_id=current_user.id,
        action='REQ_DELETE',
        description=f'Deleted requirement: {req.title}',
        is_client_visible=False,
    ))
    db.session.delete(req)
    db.session.commit()
    return jsonify({'success': True, 'id': req_id})


@bp.route('/projects/<int:project_id>/requirements')
@login_required
def kanban(project_id):
    project = _project_or_403(project_id)
    columns = _build_columns(project.id)
    return render_template(
        'architect/kanban.html',
        project=project,
        active_project=project,
        active_tab='requirements',
        columns=columns,
        kanban_states=KANBAN_STATES,
        kanban_labels=KANBAN_LABELS,
    )


@bp.route('/projects/<int:project_id>/requirements/<int:req_id>')
@login_required
def requirement_detail(project_id, req_id):
    requirement, project = _get_requirement_or_403(project_id, req_id)
    comments = RequirementComment.query.filter_by(requirement_id=req_id).order_by(RequirementComment.created_at.asc()).all()
    return jsonify({
        'id': requirement.id,
        'title': requirement.title,
        'description': requirement.description,
        'status': requirement.status,
        'category': requirement.category,
        'source': requirement.source,
        'raised_by_role': requirement.raised_by_role if hasattr(requirement, 'raised_by_role') else None,
        'created_at': requirement.created_at.isoformat() if requirement.created_at else None,
        'comments': [
            {
                'id': comment.id,
                'content': comment.content,
                'role': comment.role,
                'created_at': comment.created_at.isoformat() if comment.created_at else None,
            }
            for comment in comments
        ],
    })


@bp.route('/projects/<int:project_id>/requirements/<int:req_id>/delete', methods=['POST', 'DELETE'])
@login_required
def delete_requirement(project_id, req_id):
    project = _project_or_403(project_id)
    if current_user.role != 'architect':
        return jsonify({'error': 'Forbidden'}), 403

    requirement = Requirement.query.filter_by(id=req_id, project_id=project_id).first_or_404()
    db.session.delete(requirement)
    db.session.commit()
    return jsonify({'success': True})


@bp.route('/projects/<int:project_id>/requirements/<int:req_id>/comments', methods=['POST'])
@login_required
def add_requirement_comment(project_id, req_id):
    requirement, project = _get_requirement_or_403(project_id, req_id)

    payload = request.get_json(silent=True) or {}
    content = (payload.get('content') or request.form.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'Content required'}), 400

    comment = RequirementComment(
        requirement_id=requirement.id,
        content=content[:4000],
        author_id=current_user.id,
        role=current_user.role,
        created_at=datetime.utcnow(),
        parent_id=payload.get('parent_id') or request.form.get('parent_id'),
    )
    db.session.add(comment)
    db.session.commit()

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': True, 'comment_id': comment.id})

    flash('Requirement comment added.', 'success')
    if requirement.meeting_id:
        return redirect(url_for('meetings.project_mom', project_id=project.id, meeting_id=requirement.meeting_id))
    return redirect(url_for('requirements.kanban', project_id=project.id))


@bp.route('/projects/<int:project_id>/requirements/<int:req_id>/update-status', methods=['POST'])
@login_required
def update_status(project_id, req_id):
    requirement, project = _get_requirement_or_403(project_id, req_id)

    payload = request.get_json(silent=True) or {}
    new_status = request.form.get('status') or payload.get('status')
    if new_status not in KANBAN_STATES:
        return jsonify({'error': 'invalid status'}), 400

    allowed_by_role = {
        'client': ['new'],
        'architect': ['new', 'confirmed', 'in_progress', 'done'],
    }
    if new_status not in allowed_by_role.get(current_user.role, []):
        return jsonify({'error': 'Forbidden'}), 403

    requirement.status = new_status
    _log_meeting_event(requirement.meeting_id, 'REQUIREMENT_STATUS', f'{requirement.title} → {KANBAN_LABELS[new_status]}')
    db.session.commit()

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'id': requirement.id, 'status': requirement.status})

    flash(f'Moved to {KANBAN_LABELS[new_status]}.', 'success')
    if request.form.get('next') == 'mom' and requirement.meeting_id:
        return redirect(url_for('meetings.project_mom', project_id=project.id, meeting_id=requirement.meeting_id))
    if request.form.get('next') == 'detail' and requirement.meeting_id:
        return redirect(url_for('meetings.project_detail', project_id=project.id, meeting_id=requirement.meeting_id))
    return redirect(url_for('requirements.kanban', project_id=project.id))
