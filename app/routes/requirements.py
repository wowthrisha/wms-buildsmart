from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.auth import roles_required
from app.models import AuditLog, Comment, Meeting, MeetingLog, Project, Requirement
from app.notifications_service import notify_user

bp = Blueprint('requirements', __name__)

KANBAN_STATES = ['new', 'confirmed', 'in_progress', 'done']
KANBAN_LABELS = {
    'new': 'New',
    'confirmed': 'Confirmed',
    'in_progress': 'In Progress',
    'done': 'Done',
}


# ── helpers ───────────────────────────────────────────────────────────────

def _project_access_check(project):
    if current_user.role == 'architect' and project.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client' and project.client_id != current_user.id:
        abort(403)


def _get_meeting_or_403(meeting_id):
    meeting = Meeting.query.get_or_404(meeting_id)
    _project_access_check(meeting.project)
    return meeting, meeting.project


def _notify_other_party(project, title, body):
    try:
        other_id = project.client_id if current_user.role == 'architect' else project.architect_id
        if other_id:
            notify_user(other_id, title, body)
    except Exception:
        pass


def _meeting_redirect(meeting_id, fallback_project_id=None):
    target = request.form.get('next') or request.args.get('next')
    if target == 'mom':
        return redirect(url_for('meetings.mom_workspace', meeting_id=meeting_id))
    if meeting_id:
        return redirect(url_for('meetings.detail', meeting_id=meeting_id))
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


# ── Comment routes ─────────────────────────────────────────────────────────

@bp.route('/meetings/<int:meeting_id>/comment', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def add_comment(meeting_id):
    meeting, project = _get_meeting_or_403(meeting_id)

    content = (request.form.get('content') or '').strip()
    if not content:
        flash('Comment cannot be empty.', 'error')
        return _meeting_redirect(meeting_id)

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
    return _meeting_redirect(meeting.id)


@bp.route('/comments/<int:comment_id>/convert', methods=['POST'])
@login_required
def convert_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    meeting = comment.meeting
    project = meeting.project
    _project_access_check(project)

    if comment.requirement_id:
        flash('That comment is already linked to a requirement.', 'info')
        return _meeting_redirect(meeting.id)

    title = (request.form.get('req_title') or comment.content[:80] or 'Client Requirement').strip()[:255]
    category = (request.form.get('req_category') or 'General').strip()[:100]

    requirement = Requirement(
        project_id=project.id,
        meeting_id=meeting.id,
        title=title,
        description=comment.content,
        category=category,
        status='new',
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
    return _meeting_redirect(meeting.id)


# ── Requirement direct-add ─────────────────────────────────────────────────

@bp.route('/projects/<int:project_id>/requirements/add', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def add_requirement(project_id):
    project = Project.query.get_or_404(project_id)
    _project_access_check(project)

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

    if meeting_id:
        meeting = Meeting.query.get_or_404(meeting_id)
        if meeting.project_id != project.id:
            abort(403)
    else:
        meeting = None

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


# ── Kanban ─────────────────────────────────────────────────────────────────

@bp.route('/projects/<int:project_id>/kanban')
@login_required
def kanban(project_id):
    project = Project.query.get_or_404(project_id)
    _project_access_check(project)
    columns = _build_columns(project.id)
    return render_template(
        'architect/kanban.html',
        project=project,
        columns=columns,
        kanban_states=KANBAN_STATES,
        kanban_labels=KANBAN_LABELS,
    )


@bp.route('/projects/<int:project_id>/kanban/data')
@login_required
def kanban_data(project_id):
    project = Project.query.get_or_404(project_id)
    _project_access_check(project)
    return jsonify(_build_columns_json(project.id))


@bp.route('/requirements/<int:req_id>/update-status', methods=['POST'])
@login_required
def update_status(req_id):
    requirement = Requirement.query.get_or_404(req_id)
    project = requirement.project
    _project_access_check(project)

    payload = request.get_json(silent=True) or {}
    new_status = request.form.get('status') or payload.get('status')
    if new_status not in KANBAN_STATES:
        return jsonify({'error': 'invalid status'}), 400

    requirement.status = new_status
    _log_meeting_event(requirement.meeting_id, 'REQUIREMENT_STATUS', f'{requirement.title} → {KANBAN_LABELS[new_status]}')
    db.session.commit()

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'id': requirement.id, 'status': requirement.status})

    flash(f'Moved to {KANBAN_LABELS[new_status]}.', 'success')
    if request.form.get('next') == 'mom' and requirement.meeting_id:
        return redirect(url_for('meetings.mom_workspace', meeting_id=requirement.meeting_id))
    if request.form.get('next') == 'detail' and requirement.meeting_id:
        return redirect(url_for('meetings.detail', meeting_id=requirement.meeting_id))
    return redirect(url_for('requirements.kanban', project_id=project.id))


# ── internals ──────────────────────────────────────────────────────────────

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
