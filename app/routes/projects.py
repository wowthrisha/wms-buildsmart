from flask import Blueprint, render_template, redirect, request, jsonify, abort, url_for
from flask_login import login_required, current_user
import json
from app import db
from app.compliance_service import build_compliance_tree, compliance_summary, coverage_by_category, ensure_project_compliance_items
from app.models import Project, User, Document, Meeting, ComplianceItem, Requirement
from app.auth import require_architect, roles_required
from datetime import datetime

bp = Blueprint('projects', __name__)


def _workspace_response(project_id, *, active_tab='overview'):
    require_architect()
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id:
        abort(403)

    from app.models import AuditLog, PaymentLog, PlotAnalysis

    latest_pa = (PlotAnalysis.query
                 .filter_by(project_id=p.id)
                 .order_by(PlotAnalysis.created_at.desc())
                 .first())
    try:
        latest_pa_result = json.loads(latest_pa.result_payload) if latest_pa and latest_pa.result_payload else None
    except Exception:
        latest_pa_result = None
    pa_count = PlotAnalysis.query.filter_by(project_id=p.id).count()
    checklist_items = ensure_project_compliance_items(p.id)
    compliance_tree = build_compliance_tree(p)
    compliance_stats = compliance_summary(p)
    compliance_coverage = coverage_by_category(p)
    docs_list = (Document.query
                 .filter_by(project_id=p.id)
                 .order_by(Document.created_at.desc())
                 .all())
    audit_logs = (AuditLog.query
                  .filter_by(project_id=p.id)
                  .order_by(AuditLog.created_at.desc())
                  .all())
    all_meetings = (Meeting.query
                    .filter_by(project_id=p.id)
                    .order_by(Meeting.created_at.desc())
                    .all())
    payment_logs = (PaymentLog.query
                    .filter_by(project_id=p.id)
                    .order_by(PaymentLog.created_at.desc(), PaymentLog.id.desc())
                    .all())
    db.session.commit()
    return render_template(
        'architect/project_workspace.html',
        project=p,
        active_project=p,
        active_tab=active_tab,
        Meeting=Meeting,
        latest_pa=latest_pa,
        latest_pa_result=latest_pa_result,
        pa_count=pa_count,
        checklist_items=checklist_items,
        compliance_tree=compliance_tree,
        compliance_stats=compliance_stats,
        compliance_coverage=compliance_coverage,
        docs_list=docs_list,
        audit_logs=audit_logs,
        all_meetings=all_meetings,
        payment_logs=payment_logs,
    )

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
    remaining_compliance = ComplianceItem.query.filter(ComplianceItem.project_id.in_(project_ids), ComplianceItem.status == 'missing').count() if project_ids else 0
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
    
    ensure_project_compliance_items(p.id)
    db.session.commit()

    return redirect(f'/projects/{p.id}')

@bp.route('/projects/<int:project_id>')
@login_required
def workspace(project_id):
    _tab = request.args.get('tab')
    if _tab == 'payments':
        return redirect(url_for('payments.project_overview', project_id=project_id))
    if _tab == 'meetings':
        return redirect(url_for('meetings.project_meetings', project_id=project_id))
    if _tab == 'references':
        return redirect(url_for('projects.ref_index', project_id=project_id))
    if _tab == 'plot':
        return redirect(url_for('plot_analysis.project_overview', project_id=project_id))
    if _tab == 'documents':
        return redirect(url_for('projects.project_documents', project_id=project_id))
    if _tab == 'compliance':
        return redirect(url_for('projects.project_compliance', project_id=project_id))
    if _tab == 'activity':
        return redirect(url_for('projects.project_activity', project_id=project_id))
    if _tab == 'requirements':
        return redirect(url_for('requirements.kanban', project_id=project_id))
    return _workspace_response(project_id, active_tab='overview')


@bp.route('/projects/<int:project_id>/overview')
@login_required
def project_overview_tab(project_id):
    return _workspace_response(project_id, active_tab='overview')


@bp.route('/projects/<int:project_id>/documents')
@login_required
def project_documents(project_id):
    return _workspace_response(project_id, active_tab='documents')


@bp.route('/projects/<int:project_id>/compliance')
@login_required
def project_compliance(project_id):
    return _workspace_response(project_id, active_tab='compliance')


@bp.route('/projects/<int:project_id>/activity')
@login_required
def project_activity(project_id):
    return _workspace_response(project_id, active_tab='activity')

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
                mid = request.form.get('meeting_id', type=int)
                return redirect(url_for('meetings.project_mom', project_id=project_id, meeting_id=mid))
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
            return redirect(url_for('meetings.project_mom', project_id=project_id, meeting_id=meeting.id))

    return redirect(request.referrer or '/dashboard')

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
@roles_required(['architect', 'client'])
def upload_reference(project_id):
    """Upload a reference image, run CLIP synchronously, update RequirementCard."""
    from app.storage import validate_secure_mime, save_file_securely
    from app.models import VisualReference
    from app.vision import analyse_image
    from app.requirement_fusion import generate_requirement_card
    from flask import current_app
    import os

    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403
    if current_user.role == 'client' and p.client_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403

    file    = request.files.get('file')
    caption = request.form.get('caption', '').strip()

    if not file or not file.filename:
        return jsonify({'error': 'No file provided'}), 400

    if not validate_secure_mime(file, ['image/jpeg', 'image/png', 'image/webp']):
        return jsonify({'error': 'Only JPG, PNG, WebP images allowed'}), 400

    filename = save_file_securely(file, filename_prefix='ref')

    ref = VisualReference(
        project_id=p.id,
        filename=filename,
        file_path=filename,   # same value — used by serve route
        caption=caption,
        uploader_id=current_user.id,
        uploader_role=current_user.role,
        source_url=request.form.get('source_url', '').strip(),
    )
    db.session.add(ref)
    db.session.flush()

    # Run CLIP synchronously (< 1 s on CPU, fine for demo)
    abs_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(abs_path):
        vision = analyse_image(abs_path)
        ref.tags_json     = json.dumps(vision.get('tags', []))
        ref.style_primary = vision.get('style_primary', '')
        ref.colors_json   = vision.get('colors_json', '[]')
        ref.clip_scores   = vision.get('clip_scores', '{}')
    else:
        ref.tags_json     = '[]'
        ref.style_primary = ''
        ref.colors_json   = '[]'
        ref.clip_scores   = '{}'

    db.session.commit()

    # Update project-level RequirementCard
    card = None
    try:
        card = generate_requirement_card(project_id)
    except Exception as e:
        print(f"[upload_reference] fusion error: {e}")

    return jsonify({
        'success': True,
        'reference_id': ref.id,
        'tags': json.loads(ref.tags_json or '[]'),
        'style_primary': ref.style_primary,
        'card_generated': card is not None,
        'divergence_score': card.divergence_score if card else None,
        'feasibility_pct':  card.feasibility_pct  if card else None,
    })


@bp.route('/projects/<int:project_id>/references', methods=['GET'])
@login_required
@roles_required(['architect', 'client'])
def ref_index(project_id):
    """Visual Reference moodboard — visible to both architect and client."""
    from app.models import VisualReference, RequirementCard

    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    if current_user.role == 'client'    and p.client_id    != current_user.id: abort(403)

    refs = (VisualReference.query
            .filter_by(project_id=project_id)
            .order_by(VisualReference.created_at.desc())
            .all())

    card = RequirementCard.query.filter_by(project_id=project_id).first()

    refs_data = []
    for ref in refs:
        refs_data.append({
            'id':            ref.id,
            'filename':      ref.filename,
            'caption':       ref.caption,
            'uploader_role': ref.uploader_role or 'architect',
            'style_primary': ref.style_primary or 'Analysing...',
            'tags':          json.loads(ref.tags_json   or '[]'),
            'colors':        json.loads(ref.colors_json or '[]'),
            'source_url':    ref.source_url or '',
            'uploader_id':   ref.uploader_id,
            'created_at':    ref.created_at,
        })

    return render_template(
        'architect/references.html',
        project=p,
        active_project=p,
        active_tab='references',
        refs=refs_data,
        card=card,
        client_refs=[r for r in refs_data if r['uploader_role'] == 'client'],
        arch_refs=[r   for r in refs_data if r['uploader_role'] == 'architect'],
    )


@bp.route('/projects/<int:project_id>/references/<int:ref_id>/delete', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def ref_delete(project_id, ref_id):
    """Owner or architect can delete a reference."""
    from app.models import VisualReference

    ref = VisualReference.query.filter_by(id=ref_id, project_id=project_id).first_or_404()
    if current_user.id != ref.uploader_id and current_user.role != 'architect':
        abort(403)

    db.session.delete(ref)
    db.session.commit()
    return jsonify({'success': True})


@bp.route('/projects/<int:project_id>/requirement-card', methods=['GET'])
@login_required
@roles_required(['architect', 'client'])
def req_card(project_id):
    """Returns the current RequirementCard as JSON."""
    from app.models import RequirementCard

    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    if current_user.role == 'client'    and p.client_id    != current_user.id: abort(403)

    card = RequirementCard.query.filter_by(project_id=project_id).first()
    if not card:
        return jsonify({'card': None})

    return jsonify({'card': {
        'visual_style':   card.visual_style,
        'materials':      json.loads(card.materials_json  or '[]'),
        'spatial_tags':   json.loads(card.spatial_tags    or '[]'),
        'nlp_intents':    json.loads(card.nlp_intent      or '[]'),
        'conflicts':      json.loads(card.conflicts_json  or '[]'),
        'divergence_score': card.divergence_score,
        'feasibility_pct':  card.feasibility_pct,
        'generated_at': card.generated_at.isoformat() if card.generated_at else None,
    }})


@bp.route('/projects/<int:project_id>/requirement-card/generate', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def req_generate(project_id):
    """Triggers full dual-pipeline reanalysis and regenerates the RequirementCard."""
    from app.requirement_fusion import generate_requirement_card
    from app.models import VisualReference

    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    if current_user.role == 'client'    and p.client_id    != current_user.id: abort(403)

    ref_count = VisualReference.query.filter_by(project_id=project_id).count()
    if ref_count == 0:
        return jsonify({'error': 'No references uploaded yet. Add at least one image first.'}), 400

    try:
        card = generate_requirement_card(project_id)
        if not card:
            return jsonify({'error': 'Card generation failed. Try again.'}), 500
        return jsonify({
            'success': True,
            'visual_style':     card.visual_style,
            'feasibility_pct':  card.feasibility_pct,
            'divergence_score': card.divergence_score,
            'conflicts':        json.loads(card.conflicts_json or '[]'),
            'spatial_tags':     json.loads(card.spatial_tags   or '[]'),
            'materials':        json.loads(card.materials_json or '[]'),
            'generated_at': card.generated_at.strftime('%d %b %Y %H:%M') if card.generated_at else '',
            'ref_count': ref_count,
        })
    except Exception as e:
        print(f"[req_generate] error: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/projects/<int:project_id>/references/<int:ref_id>/image')
@login_required
@roles_required(['architect', 'client'])
def serve_reference_image(project_id, ref_id):
    """Serve a reference image securely."""
    from app.models import VisualReference
    from app.storage import send_file_securely

    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id: abort(403)
    if current_user.role == 'client'    and p.client_id    != current_user.id: abort(403)

    ref = VisualReference.query.filter_by(id=ref_id, project_id=project_id).first_or_404()
    path = ref.file_path or ref.filename
    if not path:
        abort(404)
    return send_file_securely(path)

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
