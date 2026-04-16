from contextlib import contextmanager
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import HTTPException

from app import db
from app.models import (
    AuditLog,
    Comment,
    Document,
    Meeting,
    MeetingLog,
    MeetingRequest,
    Project,
    ProjectImage,
    Requirement,
)
from app.notifications_service import notify_user_comms
from app.time_utils import utc_now

bp = Blueprint('meetings', __name__)

REQUEST_STATUSES = ['pending_request', 'requested']
PROPOSED_STATUSES = ['proposed', 'awaiting_client']
COUNTER_STATUSES = ['counter_proposed', 'countered']
CONFIRMED_STATUSES = ['confirmed']
COMPLETED_STATUSES = ['completed']
ACTIVE_STATUSES = PROPOSED_STATUSES + COUNTER_STATUSES + CONFIRMED_STATUSES
OPEN_STATUSES = REQUEST_STATUSES + ACTIVE_STATUSES + COMPLETED_STATUSES


@contextmanager
def _transaction():
    try:
        with db.session.begin_nested():
            yield
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ── Internal helpers ──────────────────────────────────────────────────────

def _parse_slot(value):
    if not value:
        return None
    if 'T' in value:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M')
    return datetime.strptime(value, '%Y-%m-%d %H:%M')


def _status_bucket(meeting):
    return meeting.normalized_status if hasattr(meeting, 'normalized_status') else meeting.status


def _log(meeting_id, action, note=''):
    try:
        db.session.add(MeetingLog(
            meeting_id=meeting_id,
            actor_id=current_user.id,
            action=action,
            note=note,
        ))
    except Exception:
        pass


def _audit(project_id, action, description, is_client_visible=True):
    db.session.add(AuditLog(
        project_id=project_id,
        actor_id=current_user.id,
        action=action,
        description=description,
        is_client_visible=is_client_visible,
    ))


def _client_projects():
    return Project.query.options(joinedload(Project.architect)).filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).all()


def _architect_projects():
    return Project.query.options(joinedload(Project.client)).filter_by(architect_id=current_user.id).order_by(Project.updated_at.desc()).all()


def _user_projects():
    return _architect_projects() if current_user.role == 'architect' else _client_projects()


def _get_user_meetings():
    if current_user.role == 'architect':
        query = Meeting.query.options(joinedload(Meeting.project).joinedload(Project.client)).join(Project).filter(Project.architect_id == current_user.id)
    else:
        project_ids = [p.id for p in _client_projects()]
        if not project_ids:
            return []
        query = Meeting.query.options(joinedload(Meeting.project).joinedload(Project.architect)).filter(Meeting.project_id.in_(project_ids))
    return query.order_by(Meeting.created_at.desc(), Meeting.id.desc()).all()


def _access_check(meeting):
    project = meeting.project
    if current_user.role == 'architect' and project.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client' and project.client_id != current_user.id:
        abort(403)


def _meeting_or_404(meeting_id, project_id=None):
    if project_id is None:
        meeting = Meeting.query.get_or_404(meeting_id)
    else:
        meeting = Meeting.query.filter_by(id=meeting_id, project_id=project_id).first_or_404()
    _access_check(meeting)
    return meeting


def _meeting_counts(meetings):
    counts = {
        'pending_requests': 0,
        'proposed': 0,
        'confirmed': 0,
        'completed': 0,
    }
    for meeting in meetings:
        status = _status_bucket(meeting)
        if status == 'pending_request':
            counts['pending_requests'] += 1
        elif status in ('proposed', 'counter_proposed'):
            counts['proposed'] += 1
        elif status == 'confirmed':
            counts['confirmed'] += 1
        elif status == 'completed':
            counts['completed'] += 1
    return counts


def _format_slot_summary(meeting):
    values = []
    for slot in meeting.proposed_slots:
        values.append(slot.strftime('%d %b %Y · %H:%M'))
    return values


def _upcoming_confirmed(meetings):
    rows = [m for m in meetings if _status_bucket(m) == 'confirmed']
    return sorted(rows, key=lambda meeting: meeting.confirmed_time or meeting.confirmed_slot or meeting.created_at)


def _completed_meetings(meetings):
    rows = [m for m in meetings if _status_bucket(m) == 'completed']
    return sorted(rows, key=lambda meeting: meeting.completed_at or meeting.confirmed_at or meeting.created_at, reverse=True)


def _requested_meetings(meetings):
    return [m for m in meetings if _status_bucket(m) == 'pending_request']


def _client_request_feed(meetings):
    return [m for m in meetings if _status_bucket(m) in ('pending_request', 'proposed', 'counter_proposed')]


def _meeting_detail_payload(meeting):
    requirements = Requirement.query.filter_by(meeting_id=meeting.id).order_by(Requirement.created_at.desc()).all()
    root_comments = meeting.comments.filter_by(parent_id=None).order_by(Comment.created_at.asc()).all()
    logs = meeting.logs.order_by(MeetingLog.created_at.desc()).all()
    documents = Document.query.filter_by(meeting_id=meeting.id).order_by(Document.created_at.desc()).all()
    images = ProjectImage.query.filter_by(meeting_id=meeting.id).order_by(ProjectImage.created_at.desc()).all()
    return {
        'requirements': requirements,
        'comments': root_comments,
        'logs': logs,
        'documents': documents,
        'images': images,
    }


def _redirect_after_detail(meeting):
    target = request.form.get('next') or request.args.get('next')
    if target == 'mom':
        return redirect(url_for('meetings.project_mom', project_id=meeting.project_id, meeting_id=meeting.id))
    return redirect(url_for('meetings.project_detail', project_id=meeting.project_id, meeting_id=meeting.id))


def _notify_counterparty(project, title, body):
    if current_user.role == 'architect' and project.client:
        notify_user_comms(project.client, title, body)
    elif current_user.role == 'client' and project.architect:
        notify_user_comms(project.architect, title, body)


# ── Meetings overview ───────────────────────────────────────────────────────

@bp.route('/meetings')
@login_required
def index():
    meetings = _get_user_meetings()
    projects = _user_projects()
    return render_template(
        'meetings_overview.html',
        meetings=meetings,
        projects=projects,
    )



@bp.route('/projects/<int:project_id>/meetings')
@login_required
def project_meetings(project_id):
    project = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and project.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client' and project.client_id != current_user.id:
        abort(403)
    meetings_list = Meeting.query.filter_by(project_id=project.id).order_by(Meeting.created_at.desc()).all()
    return render_template(
        'architect/project_meetings.html',
        project=project,
        active_project=project,
        active_tab='meetings',
        meetings=meetings_list,
    )


@bp.route('/projects/<int:project_id>/meetings/<int:meeting_id>')
@login_required
def project_detail(project_id, meeting_id):
    meeting = _meeting_or_404(meeting_id, project_id=project_id)
    payload = _meeting_detail_payload(meeting)
    return render_template(
        'meeting_detail.html',
        meeting=meeting,
        project=meeting.project,
        active_project=meeting.project,
        active_tab='meetings',
        requirements=payload['requirements'],
        comments=payload['comments'],
        logs=payload['logs'],
        documents=payload['documents'],
        images=payload['images'],
        slot_summary=_format_slot_summary(meeting),
    )


@bp.route('/projects/<int:project_id>/meetings/<int:meeting_id>/mom')
@login_required
def project_mom(project_id, meeting_id):
    meeting = _meeting_or_404(meeting_id, project_id=project_id)
    payload = _meeting_detail_payload(meeting)
    new_requirements = [req for req in payload['requirements'] if req.status == 'new']
    return render_template(
        'mom_workspace.html',
        meeting=meeting,
        project=meeting.project,
        active_project=meeting.project,
        active_tab='meetings',
        requirements=payload['requirements'],
        new_requirements=new_requirements,
        comments=payload['comments'],
        logs=payload['logs'],
        documents=payload['documents'],
        images=payload['images'],
    )


@bp.route('/meetings/<int:meeting_id>')
@login_required
def detail(meeting_id):
    meeting = _meeting_or_404(meeting_id)
    payload = _meeting_detail_payload(meeting)
    return render_template(
        'meeting_detail.html',
        meeting=meeting,
        project=meeting.project,
        active_project=meeting.project,
        active_tab='meetings',
        requirements=payload['requirements'],
        comments=payload['comments'],
        logs=payload['logs'],
        documents=payload['documents'],
        images=payload['images'],
        slot_summary=_format_slot_summary(meeting),
    )


@bp.route('/meetings/<int:meeting_id>/mom')
@login_required
def mom_workspace(meeting_id):
    meeting = _meeting_or_404(meeting_id)
    payload = _meeting_detail_payload(meeting)
    new_requirements = [req for req in payload['requirements'] if req.status == 'new']
    return render_template(
        'mom_workspace.html',
        meeting=meeting,
        project=meeting.project,
        active_project=meeting.project,
        active_tab='meetings',
        requirements=payload['requirements'],
        new_requirements=new_requirements,
        comments=payload['comments'],
        logs=payload['logs'],
        documents=payload['documents'],
        images=payload['images'],
    )


# ── Mutations ───────────────────────────────────────────────────────────────

@bp.route('/projects/<int:project_id>/meetings/request', methods=['POST'])
@login_required
def request_meeting(project_id):
    try:
        project = Project.query.get_or_404(project_id)
        if current_user.role != 'client' or project.client_id != current_user.id:
            abort(403)

        title = (request.form.get('title') or '').strip()[:200]
        notes = (request.form.get('notes') or request.form.get('description') or '').strip()[:1000]
        preferred_1 = _parse_slot(request.form.get('preferred_1'))
        preferred_2 = _parse_slot(request.form.get('preferred_2'))
        if not title:
            flash('Please add a meeting topic.', 'error')
            return redirect(url_for('meetings.project_meetings', project_id=project_id))

        with _transaction():
            meeting = Meeting(
                project_id=project.id,
                title=title,
                status='pending_request',
                description=notes,
                slot_1=preferred_1,
                slot_2=preferred_2,
            )
            db.session.add(meeting)
            db.session.flush()

            db.session.add(MeetingRequest(
                meeting_id=meeting.id,
                project_id=project.id,
                requester_id=current_user.id,
                description=notes,
            ))
            _log(meeting.id, 'REQUESTED', title)
            _audit(project.id, 'MEETING_REQUEST', f'{current_user.name} requested a meeting for {project.name}: {title}.')

        try:
            if project.architect:
                notify_user_comms(
                    project.architect,
                    'Meeting Requested',
                    f'{current_user.name} requested a meeting for {project.name}: {title}',
                )
        except Exception:
            pass

        flash('Meeting request submitted.', 'success')
        return redirect(url_for('meetings.project_meetings', project_id=project.id))
    except HTTPException:
        raise
    except ValueError:
        db.session.rollback()
        flash('One of the preferred date values was invalid.', 'error')
        return redirect(url_for('meetings.project_meetings', project_id=project_id))
    except Exception:
        db.session.rollback()
        flash('Something went wrong while requesting the meeting.', 'error')
        return redirect(url_for('meetings.project_meetings', project_id=project_id))


@bp.route('/projects/<int:project_id>/meetings/propose', methods=['POST'])
@login_required
def propose(project_id):
    try:
        project = Project.query.get_or_404(project_id)
        if current_user.role != 'architect' or project.architect_id != current_user.id:
            abort(403)

        slots = [_parse_slot(request.form.get(f'slot_{idx}')) for idx in range(1, 4)]
        title = (request.form.get('title') or '').strip()[:200]
        description = (request.form.get('notes') or request.form.get('description') or '').strip()[:1000]
        if not any(slots):
            flash('Add at least one proposed slot.', 'error')
            return redirect(url_for('meetings.project_meetings', project_id=project_id))

        meeting = None
        meeting_id = (request.form.get('meeting_id') or '').strip()
        if meeting_id:
            meeting = Meeting.query.filter_by(id=int(meeting_id), project_id=project.id).first_or_404()
        elif not title:
            flash('Add a meeting topic before sending slots.', 'error')
            return redirect(url_for('meetings.project_meetings', project_id=project_id))
        with _transaction():
            if meeting is None:
                meeting = Meeting(project_id=project.id, title=title, status='proposed', description=description)
                db.session.add(meeting)
                db.session.flush()

            meeting.slot_1, meeting.slot_2, meeting.slot_3 = slots
            meeting.status = 'proposed'
            if title:
                meeting.title = title
            if description and not meeting.description:
                meeting.description = description
            if not meeting.title:
                meeting.title = meeting.requested_description or f'Meeting #{meeting.id}'

            if not meeting.request_record and (meeting.description or meeting.title):
                db.session.add(MeetingRequest(
                    meeting_id=meeting.id,
                    project_id=project.id,
                    requester_id=project.client_id or current_user.id,
                    description=meeting.description or meeting.title,
                ))

            slot_note = ', '.join(slot.strftime('%d %b %Y %H:%M') for slot in meeting.proposed_slots)
            _log(meeting.id, 'PROPOSED', slot_note)
            _audit(project.id, 'MEETING_PROPOSE', f'Proposed slots for meeting #{meeting.id} in {project.name}.')

        try:
            if project.client:
                notify_user_comms(
                    project.client,
                    'Slots Proposed',
                    f'Your architect proposed meeting slots for {project.name}. Review meeting #{meeting.id} to pick one.',
                )
        except Exception:
            pass

        flash('Meeting slots proposed.', 'success')
        return redirect(url_for('meetings.project_meetings', project_id=project_id))
    except HTTPException:
        raise
    except ValueError:
        db.session.rollback()
        flash('One of the date/time values was invalid.', 'error')
        return redirect(url_for('meetings.project_meetings', project_id=project_id))
    except Exception:
        db.session.rollback()
        flash('Something went wrong while proposing slots.', 'error')
        return redirect(url_for('meetings.project_meetings', project_id=project_id))


@bp.route('/projects/<int:project_id>/meetings/<int:meeting_id>/confirm', methods=['POST'])
@bp.route('/meetings/<int:meeting_id>/confirm', methods=['POST'])
@login_required
def confirm(meeting_id, project_id=None):
    try:
        meeting = _meeting_or_404(meeting_id, project_id=project_id)

        selected = (
            request.form.get('selected_slot')
            or
            request.form.get('slot')
            or request.form.get('slot_choice')
            or request.form.get('slot_idx')
            or 'slot_1'
        )
        slot_map = {
            '1': 'slot_1',
            '2': 'slot_2',
            '3': 'slot_3',
            'slot_1': 'slot_1',
            'slot_2': 'slot_2',
            'slot_3': 'slot_3',
            'counter_slot': 'counter_slot',
        }
        slot_attr = slot_map.get(selected)
        if slot_attr is None:
            flash('Select a valid meeting slot.', 'error')
            return _redirect_after_detail(meeting)

        if current_user.role == 'client' and slot_attr == 'counter_slot':
            flash('Clients can confirm one of the proposed slots only.', 'error')
            return _redirect_after_detail(meeting)

        if current_user.role == 'architect' and slot_attr == 'counter_slot' and _status_bucket(meeting) != 'counter_proposed':
            flash('There is no client counter proposal to approve.', 'error')
            return _redirect_after_detail(meeting)

        confirmed_time = getattr(meeting, slot_attr, None)
        if not confirmed_time:
            flash('That slot is not available on this meeting.', 'error')
            return _redirect_after_detail(meeting)

        with _transaction():
            meeting.status = 'confirmed'
            meeting.confirmed_time = confirmed_time
            meeting.confirmed_slot = confirmed_time
            meeting.confirmed_at = utc_now()
            if not meeting.outcome:
                meeting.outcome = 'Scheduled'

            note = confirmed_time.strftime('%d %b %Y · %H:%M')
            _log(meeting.id, 'CONFIRMED', note)
            _audit(meeting.project_id, 'MEETING_CONFIRM', f'Meeting #{meeting.id} confirmed for {note}.')

        try:
            _notify_counterparty(meeting.project, 'Meeting Confirmed', f'Meeting #{meeting.id} is confirmed for {note}.')
        except Exception:
            pass

        flash('Meeting confirmed.', 'success')
        return redirect(url_for('meetings.project_detail', project_id=meeting.project_id, meeting_id=meeting.id))
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        flash('Something went wrong while confirming the meeting.', 'error')
        return redirect(request.referrer or url_for('meetings.index'))


@bp.route('/projects/<int:project_id>/meetings/<int:meeting_id>/counter', methods=['POST'])
@bp.route('/meetings/<int:meeting_id>/counter', methods=['POST'])
@login_required
def counter(meeting_id, project_id=None):
    try:
        meeting = _meeting_or_404(meeting_id, project_id=project_id)
        if current_user.role != 'client' or meeting.project.client_id != current_user.id:
            abort(403)

        # Enforce max 2 counter-proposals
        if (meeting.counter_count or 0) >= 2:
            flash('Maximum counter-proposals reached (2). Please confirm one of the proposed slots or ask your architect for new slots.', 'error')
            return redirect(url_for('meetings.project_detail', project_id=meeting.project_id, meeting_id=meeting.id))

        counter_value = (request.form.get('counter_slot') or '').strip()
        if not counter_value:
            flash('Please choose a counter-proposed time.', 'error')
            return redirect(url_for('meetings.project_detail', project_id=meeting.project_id, meeting_id=meeting.id))

        with _transaction():
            meeting.counter_slot = _parse_slot(counter_value)
            meeting.status = 'counter_proposed'
            meeting.counter_count = (meeting.counter_count or 0) + 1

            note = meeting.counter_slot.strftime('%d %b %Y · %H:%M')
            _log(meeting.id, 'COUNTER_PROPOSED', note)
            _audit(meeting.project_id, 'MEETING_COUNTER', f'{current_user.name} counter proposed {note} for meeting #{meeting.id}.')

        try:
            if meeting.project.architect:
                notify_user_comms(
                    meeting.project.architect,
                    'Counter Proposed',
                    f'{current_user.name} suggested {note} for meeting #{meeting.id}.',
                )
        except Exception:
            pass

        flash('Counter proposal sent.', 'success')
        return redirect(url_for('meetings.project_detail', project_id=meeting.project_id, meeting_id=meeting.id))
    except HTTPException:
        raise
    except ValueError:
        db.session.rollback()
        flash('Invalid date/time format.', 'error')
        return redirect(url_for('meetings.project_detail', project_id=meeting.project_id, meeting_id=meeting.id))
    except Exception:
        db.session.rollback()
        flash('Something went wrong while saving the counter proposal.', 'error')
        m = Meeting.query.get(meeting_id)
        if m:
            return redirect(url_for('meetings.project_detail', project_id=m.project_id, meeting_id=meeting_id))
        return redirect(url_for('meetings.index'))


@bp.route('/projects/<int:project_id>/meetings/<int:meeting_id>/notes', methods=['POST'])
@bp.route('/meetings/<int:meeting_id>/notes', methods=['POST'])
@login_required
def notes(meeting_id, project_id=None):
    try:
        meeting = _meeting_or_404(meeting_id, project_id=project_id)
        if current_user.role != 'architect' or meeting.project.architect_id != current_user.id:
            abort(403)

        content = (request.form.get('mom_content') or '').strip()
        if not content:
            flash('Add MOM content before saving.', 'error')
            return redirect(url_for('meetings.project_mom', project_id=meeting.project_id, meeting_id=meeting.id))

        with _transaction():
            meeting.mom_content = content[:20000]
            meeting.mom_date = utc_now()
            if request.form.get('outcome'):
                meeting.outcome = request.form.get('outcome', '').strip()[:255]

            _log(meeting.id, 'MOM_UPDATED', 'Minutes of meeting updated.')
            _audit(meeting.project_id, 'MOM_UPDATE', f'MOM updated for meeting #{meeting.id}.')

        flash('MOM saved.', 'success')
        return redirect(url_for('meetings.project_mom', project_id=meeting.project_id, meeting_id=meeting.id))
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        flash('Something went wrong while saving the MOM.', 'error')
        m = Meeting.query.get(meeting_id)
        if m:
            return redirect(url_for('meetings.project_mom', project_id=m.project_id, meeting_id=meeting_id))
        return redirect(url_for('meetings.index'))


@bp.route('/projects/<int:project_id>/meetings/<int:meeting_id>/complete', methods=['POST'])
@bp.route('/meetings/<int:meeting_id>/complete', methods=['POST'])
@login_required
def complete(meeting_id, project_id=None):
    try:
        meeting = _meeting_or_404(meeting_id, project_id=project_id)
        if current_user.role != 'architect' or meeting.project.architect_id != current_user.id:
            abort(403)

        with _transaction():
            meeting.status = 'completed'
            meeting.completed_at = utc_now()
            outcome = (request.form.get('outcome') or '').strip()
            if outcome:
                meeting.outcome = outcome[:255]
            elif meeting.mom_content:
                meeting.outcome = meeting.mom_content.strip().splitlines()[0][:255]
            else:
                meeting.outcome = 'Meeting completed'

            _log(meeting.id, 'COMPLETED', meeting.outcome)
            _audit(meeting.project_id, 'MEETING_COMPLETE', f'Meeting #{meeting.id} marked completed.')

        try:
            _notify_counterparty(meeting.project, 'Meeting Completed', f'Meeting #{meeting.id} has been marked completed.')
        except Exception:
            pass

        flash('Meeting moved to completed.', 'success')
        return redirect(url_for('meetings.project_meetings', project_id=meeting.project_id))
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        flash('Something went wrong while closing the meeting.', 'error')
        m = Meeting.query.get(meeting_id)
        if m:
            return redirect(url_for('meetings.project_mom', project_id=m.project_id, meeting_id=meeting_id))
        return redirect(url_for('meetings.index'))
