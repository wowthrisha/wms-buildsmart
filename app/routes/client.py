from flask import Blueprint, abort, jsonify, render_template, redirect, request, session, url_for
from flask_login import login_required, current_user
from app import csrf
from app import db
import app.assistant_service as assistant_service
from app.compliance_service import ensure_project_compliance_items
from app.models import AuditLog, Document, Meeting, Notification, Project

bp = Blueprint('client', __name__)

def get_client_project():
    if current_user.is_authenticated and current_user.role == 'client':
        active_id = session.get('active_project_id')
        if active_id:
            project = Project.query.filter_by(id=active_id, client_id=current_user.id).first()
            if project:
                return project
        return Project.query.filter_by(client_id=current_user.id).first()
    return None


@bp.route('/my_project/switch/<int:project_id>', methods=['GET', 'POST'])
@login_required
def switch_project(project_id):
    if current_user.role != 'client':
        abort(403)
    project = Project.query.filter_by(id=project_id, client_id=current_user.id).first_or_404()
    session['active_project_id'] = project.id
    return redirect(url_for('client.portal'))

@bp.route('/my_project/')
@login_required
def portal():
    if current_user.role != 'client': abort(403)
    p = get_client_project()
    if not p: return render_template('client/no_project.html')
    ensure_project_compliance_items(p.id)
    db.session.commit()
    projects = Project.query.filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).all()
    notifications = (
        Notification.query
        .filter_by(user_id=current_user.id, read=False)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template('client/portal.html', project=p, projects=projects, notifications=notifications)

@bp.route('/my_project/documents')
@login_required
def documents():
    if current_user.role != 'client': abort(403)
    p = get_client_project()
    if not p: return render_template('client/no_project.html')
    # Client sees visible docs + their own uploads (regardless of visible flag, so they see pending)
    from sqlalchemy import or_
    docs = (
        Document.query
        .filter_by(project_id=p.id)
        .filter(or_(Document.visible_to_client == True, Document.uploaded_by == current_user.id))
        .order_by(Document.created_at.desc())
        .all()
    )
    projects = Project.query.filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).all()
    return render_template('client/documents.html', project=p, projects=projects, documents=docs)

@bp.route('/my_project/plot')
@login_required
def plot_info():
    if current_user.role != 'client':
        abort(403)
    p = get_client_project()
    if not p:
        return render_template('client/no_project.html')
    return redirect(url_for('plot_analysis.client_view', project_id=p.id))

@bp.route('/my_project/meetings')
@login_required
def meetings():
    if current_user.role != 'client':
        abort(403)
    p = get_client_project()
    if not p:
        return render_template('client/no_project.html')
    meetings_list = (
        Meeting.query
        .filter_by(project_id=p.id)
        .order_by(Meeting.created_at.desc())
        .all()
    )
    projects = Project.query.filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).all()
    return render_template(
        'client/meetings.html',
        project=p,
        projects=projects,
        meetings=meetings_list,
    )

@bp.route('/my_project/updates')
@login_required
def updates():
    if current_user.role != 'client': abort(403)
    p = get_client_project()
    if not p: return render_template('client/no_project.html')
    logs = AuditLog.query.filter_by(project_id=p.id, is_client_visible=True).order_by(AuditLog.created_at.desc()).all()
    projects = Project.query.filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).all()
    return render_template('client/updates.html', project=p, projects=projects, logs=logs)

@bp.route('/my_project/payments')
@login_required
def payments():
    if current_user.role != 'client': abort(403)
    p = get_client_project()
    if not p: return render_template('client/no_project.html')
    return redirect(url_for('payments.project_overview', project_id=p.id))


@bp.route('/my_project/assistant')
@login_required
def assistant():
    if current_user.role != 'client':
        abort(403)
    project = get_client_project()
    if not project:
        return render_template('client/no_project.html')
    workspace = assistant_service.build_assistant_workspace(project, current_user)
    projects = Project.query.filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).all()
    notifications = (
        Notification.query
        .filter_by(user_id=current_user.id, read=False)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        'assistant_workspace.html',
        project=project,
        projects=projects,
        notifications=notifications,
        active_project=project,
        active_tab='assistant',
        **workspace,
    )


@bp.route('/my_project/assistant/query', methods=['POST'])
@login_required
@csrf.exempt
def assistant_query():
    if current_user.role != 'client':
        abort(403)
    project = get_client_project()
    if not project:
        return jsonify({'answer': 'No active project is selected right now.'}), 400

    payload = request.get_json(silent=True) or {}
    question = str(payload.get('question') or request.form.get('question') or '').strip()
    if not question:
        return jsonify({'answer': 'Please enter a question.'}), 400

    try:
        answer = assistant_service.answer_assistant_question(project, current_user, question)
        return jsonify({
            'answer': answer,
            'project_id': project.id,
            'project_name': project.name,
            'audience': 'client',
            'profession': (current_user.profession or '').strip() if hasattr(current_user, 'profession') else '',
        })
    except Exception as e:
        db.session.rollback()
        from flask import current_app
        current_app.logger.error(f'Client assistant query failed for project {project.id}: {e}')
        return jsonify({
            'answer': 'Could not process your question right now. Please try again in a moment.',
            'error': str(e),
        }), 500


@bp.route('/api/rag/query', methods=['POST'])
@login_required
def rag_query():
    if current_user.role != 'client':
        abort(403)
    return assistant_query()
