from flask import Blueprint, render_template, redirect, request, jsonify, abort, url_for
from flask_login import login_required, current_user
from app import db
from app.models import Project, User, Document, Meeting, ComplianceItem, Requirement
from app.auth import require_architect, roles_required
from datetime import datetime

bp = Blueprint('projects', __name__)

@bp.route('/')
@login_required
def root():
    from flask import url_for
    if current_user.role == 'architect': return redirect(url_for('projects.dashboard'))
    return redirect(url_for('client.portal'))

@bp.route('/dashboard')
@login_required
def dashboard():
    require_architect()
    projects = Project.query.filter_by(architect_id=current_user.id).order_by(Project.updated_at.desc()).all()
    project_ids = [p.id for p in projects]
    
    active_count = sum(1 for p in projects if p.status in ['Design', 'Review'])
    total_projects = len(projects)
    total_clients = User.query.filter_by(role='client').count()
    total_docs = Document.query.filter(Document.project_id.in_(project_ids)).count() if project_ids else 0
    
    # Dynamic metrics
    pending_approvals = Document.query.filter(Document.project_id.in_(project_ids), Document.approval_status == 'pending').count() if project_ids else 0
    confirmed_meetings = Meeting.query.join(Project).filter(Project.architect_id == current_user.id, Meeting.status == 'confirmed').count()
    remaining_compliance = ComplianceItem.query.filter(ComplianceItem.project_id.in_(project_ids), ComplianceItem.arch_checked == False).count() if project_ids else 0
    new_requirements = Requirement.query.filter(Requirement.project_id.in_(project_ids), Requirement.status == 'new').count() if project_ids else 0
    recent_requirements = Requirement.query.filter(Requirement.project_id.in_(project_ids)).order_by(Requirement.created_at.desc()).limit(5).all() if project_ids else []
    
    return render_template('architect/dashboard.html', 
                           projects=projects, 
                           active_count=active_count, 
                           pending_approvals=pending_approvals, 
                           confirmed_meetings=confirmed_meetings, 
                           remaining_compliance=remaining_compliance, 
                           new_requirements=new_requirements,
                           recent_requirements=recent_requirements,
                           total_projects=total_projects, 
                           total_clients=total_clients, 
                           total_docs=total_docs)

@bp.route('/projects', strict_slashes=False)
@login_required
def list_projects():
    require_architect()
    projects = Project.query.filter_by(architect_id=current_user.id).all()
    return render_template('architect/projects.html', projects=projects)

@bp.route('/projects/new', methods=['GET', 'POST'])
@login_required
def new_project():
    require_architect()
    if request.method == 'GET':
        clients = User.query.filter_by(role='client').all()
        return render_template('architect/project_new.html', clients=clients)

    name = request.form.get('name', 'New Project')
    client_id = request.form.get('client_id')
    plot_zone = request.form.get('plot_zone', request.form.get('location', 'Mixed'))

    project_client_id = int(client_id) if client_id else None

    total_budget = 0
    budget_str = request.form.get('budget', request.form.get('total_budget', '0'))
    try:
        total_budget = float(budget_str)
        if total_budget < 0 or total_budget > 1000000000: # 1B limit
            from flask import flash
            flash('Invalid budget amount.', 'error')
            return redirect(request.referrer or '/dashboard')
    except ValueError:
        pass

    p = Project(
        name=name,
        architect_id=current_user.id,
        client_id=project_client_id,
        plot_zone=plot_zone,
        status='Design',
        total_budget=total_budget
    )
    db.session.add(p)
    db.session.commit()
    
    # Add default items
    for label in ['Building Plan Approval', 'Fire NOC']:
        db.session.add(ComplianceItem(project_id=p.id, label=label))
    db.session.commit()

    return redirect(f'/projects/{p.id}')

@bp.route('/projects/create', methods=['POST'])
@login_required
def create():
    return new_project()

@bp.route('/projects/<int:project_id>')
@login_required
def workspace(project_id):
    require_architect()
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    if request.args.get('tab') == 'meetings':
        return redirect(url_for('meetings.index'))
    if request.args.get('tab') == 'payments':
        return redirect(url_for('payments.project_overview', project_id=p.id))
    return render_template('architect/project_workspace.html', project=p, active_project=p, Meeting=Meeting)

# REMOVED:
# @bp.route('/api/debug-clients')
# def debug_clients(): ... 

# update_status:
from app.notifications_service import notify_user

@bp.route('/projects/<int:project_id>/update-status', methods=['POST'])
@login_required
def update_status(project_id):
    require_architect()
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    
    new_status = request.form.get('status')
    if not new_status:
        from flask import flash
        flash('Status is required.', 'error')
        return redirect(request.referrer or '/dashboard')
    
    if new_status in Project.STATUS_ORDER:
        p.status = new_status
        db.session.commit()
        flash(f'Status updated to {new_status}.', 'success')

        # Audit
        from app.models import AuditLog
        log = AuditLog(project_id=p.id, actor_id=current_user.id, action='STATUS_CHANGE', 
                       description=f'Status changed to {new_status}')
        db.session.add(log)
        db.session.commit()

        # Notify other side
        other_id = p.client_id if current_user.id == p.architect_id else p.architect_id
        if other_id:
            try:
                notify_user(other_id, 'Project Status Updated',
                            f'{current_user.name} changed "{p.name}" status to {new_status}')
            except Exception as e:
                from flask import current_app
                current_app.logger.error(f'notify_user error: {e}')
    return redirect(request.referrer or '/dashboard')

@bp.route('/projects/<int:project_id>/delete', methods=['POST'])
@login_required
def delete(project_id):
    require_architect()
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    
    # Safely log deletion and wipe core entity
    from app.models import AuditLog
    p_name = p.name
    log = AuditLog(project_id=None, actor_id=current_user.id, action='DELETE', 
                   description=f'Deleted project: {p_name}')
    db.session.add(log)
    db.session.delete(p)
    db.session.commit()
    
    from flask import flash
    flash(f"Project '{p_name}' securely deleted.", "success")
    return redirect('/projects')

@bp.route('/projects/<int:project_id>/images/upload', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def upload_image(project_id):
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    if current_user.role == 'client' and p.client_id != current_user.id: abort(403)
    
    file = request.files.get('image')
    if file and file.filename:
        from app.storage import validate_secure_mime, save_file_securely
        if not validate_secure_mime(file, ['image/jpeg', 'image/png']):
            from flask import flash
            flash('Invalid image type. Only JPG and PNG are allowed.', 'error')
            if request.form.get('next') == 'mom' and request.form.get('meeting_id'):
                return redirect(url_for('meetings.mom_workspace', meeting_id=request.form.get('meeting_id', type=int)))
            return redirect(request.referrer or '/dashboard')
            
        filename = save_file_securely(file)
        
        meeting_id = request.form.get('meeting_id', type=int)
        requirement_id = request.form.get('requirement_id', type=int)
        if meeting_id:
            from app.models import Meeting
            meeting = Meeting.query.get_or_404(meeting_id)
            if meeting.project_id != p.id: abort(403)
        else:
            meeting = None
        if requirement_id:
            from app.models import Requirement
            requirement = Requirement.query.get_or_404(requirement_id)
            if requirement.project_id != p.id: abort(403)
        else:
            requirement = None

        from app.models import ProjectImage, MeetingLog
        img = ProjectImage(
            project_id=p.id,
            meeting_id=meeting.id if meeting else None,
            requirement_id=requirement.id if requirement else None,
            filename=filename,
            tag=request.form.get('tag', 'Reference'),
            uploader_id=current_user.id
        )
        db.session.add(img)
        
        # Audit Log
        from app.models import AuditLog
        log = AuditLog(project_id=p.id, actor_id=current_user.id, action='UPLOAD', 
                       description=f'Uploaded project image: {file.filename}')
        db.session.add(log)
        if meeting:
            db.session.add(MeetingLog(
                meeting_id=meeting.id,
                actor_id=current_user.id,
                action='IMAGE_UPLOADED',
                note=file.filename[:255],
            ))
        
        db.session.commit()
        from flask import flash
        flash('Image uploaded.', 'success')
        if request.form.get('next') == 'mom' and meeting:
            return redirect(url_for('meetings.mom_workspace', meeting_id=meeting.id))

    return redirect(request.referrer or '/dashboard')

@bp.route('/projects/<int:project_id>/images/delete/<int:image_id>', methods=['POST'])
@bp.route('/projects/<int:project_id>/images/<int:image_id>/delete', methods=['POST'])
@login_required
def delete_image(project_id, image_id):
    require_architect()
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    
    from app.models import ProjectImage
    img = ProjectImage.query.get_or_404(image_id)
    if img.project_id != p.id: abort(404)
    
    filename = img.filename
    db.session.delete(img)
    
    # Audit Log
    from app.models import AuditLog
    log = AuditLog(project_id=p.id, actor_id=current_user.id, action='DELETE', 
                   description=f'Deleted project image: {filename}')
    db.session.add(log)
    
    db.session.commit()
    
    # Delete file from disk
    import os
    from flask import current_app
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            print(f"Error deleting file {filename}: {e}")
    
    from flask import flash
    flash('Image deleted.', 'success')
    
    return redirect(request.referrer or '/dashboard')

@bp.route('/projects/<int:project_id>/references/add', methods=['POST'])
@login_required
def upload_reference(project_id):
    """Uploads a reference image and triggers the AI pipeline."""
    require_architect()
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    
    file = request.files.get('image')
    caption = request.form.get('caption', '')
    
    if file and file.filename:
        from app.storage import validate_secure_mime, save_file_securely
        import os
        # Security: MIME validation with fallback
        if not validate_secure_mime(file, ['image/jpeg', 'image/png']):
            from flask import flash
            flash('Invalid reference image. Only JPG/PNG allowed.', 'error')
            return redirect(request.referrer or '/dashboard')
            
        filename = save_file_securely(file, filename_prefix="ref")
        
        from app.models import VisualReference
        ref = VisualReference(
            project_id=p.id,
            filename=filename,
            caption=caption,
            uploader_id=current_user.id
        )
        db.session.add(ref)
        db.session.commit()
        
        # Trigger Pipeline (Async)
        from app.tasks import process_visual_reference
        try:
            # Use delay() if Celery is running, else fallback to direct (for dev simplicity)
            if os.environ.get('CELERY_BROKER_URL'):
                process_visual_reference.delay(ref.id)
            else:
                import threading
                threading.Thread(target=process_visual_reference, args=(ref.id,)).start()
        except Exception:
            pass
            
        from flask import flash
        flash('Reference uploaded. Processing intent in background...', 'success')
        
    return redirect(request.referrer or '/dashboard')

@bp.route('/projects/<int:project_id>/update-status-api', methods=['POST'])
@login_required
def update_status_api(project_id):
    require_architect()
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    data = request.get_json()
    if data and 'status' in data:
        p.status = data['status']
        db.session.commit()
    return jsonify({'ok': True, 'status': p.status})

@bp.route('/projects/<int:project_id>/toggle-compliance', methods=['POST'])
@login_required
def toggle_compliance_visibility(project_id):
    require_architect()
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    
    p.allow_client_compliance = not p.allow_client_compliance
    db.session.commit()
    from flask import flash
    flash('Compliance visibility toggled.', 'success')
    return redirect(request.referrer or '/dashboard')
