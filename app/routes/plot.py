from flask import Blueprint, request, redirect, jsonify, render_template, abort
from flask_login import login_required, current_user
from app import db
from app.models import Project, PlotData
from app.auth import require_architect, roles_required
from app.time_utils import utc_now

bp = Blueprint('plot', __name__)

@bp.route('/plot')
@login_required
def index():
    require_architect()
    projects = Project.query.filter_by(architect_id=current_user.id).all()
    return render_template('architect/plot_standalone.html', projects=projects)

@bp.route('/projects/<int:project_id>/plot/upload', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def upload(project_id):
    try:
        p = Project.query.get_or_404(project_id)
        if current_user.role == 'architect' and p.architect_id != current_user.id:
            abort(403)
        if current_user.role == 'client' and p.client_id != current_user.id:
            abort(403)
            
        file = request.files.get('sketch')
        if file and file.filename:
            from app.storage import validate_secure_mime, save_file_securely
            if not validate_secure_mime(file, ['application/pdf', 'image/jpeg', 'image/png']):
                from flask import flash
                flash('File type not allowed or malicious file detected.', 'error')
                return redirect(request.referrer or '/dashboard')
            
            filename = save_file_securely(file)
            
            if not p.plot:
                p.plot = PlotData(project_id=p.id)
                db.session.add(p.plot)
            
            p.plot.sketch_filename = filename
            p.plot.sketch_uploader = current_user.role
            
            # The previous synchronous OCR call accepted a file handle where the OCR
            # parser expects a path, so it silently failed and added no production value.
            # Keeping uploads stable is safer than running a broken extraction stub here.
            
            # Audit Log
            from app.models import AuditLog
            log = AuditLog(project_id=p.id, actor_id=current_user.id, action='UPLOAD', 
                           description=f'Uploaded plot sketch: {file.filename}')
            db.session.add(log)
            
        db.session.commit()
        from flask import flash
        flash('Saved.', 'success')
        return redirect(request.referrer or '/dashboard')
    except ValueError as e:
        db.session.rollback()
        from flask import flash
        flash(f'Invalid input: {e}', 'error')
        return redirect(request.referrer or '/dashboard')
    except Exception as e:
        db.session.rollback()
        from flask import current_app, flash
        current_app.logger.error(f'Error in {request.endpoint}: {e}')
        flash('Something went wrong. Please try again.', 'error')
        return redirect(request.referrer or '/dashboard')

@bp.route('/projects/<int:project_id>/plot/update', methods=['POST'])
@login_required
def update(project_id):
    require_architect()
    try:
        p = Project.query.get_or_404(project_id)
        if current_user.role == 'architect' and p.architect_id != current_user.id:
            abort(403)
            
        plot = p.plot
        if not plot:
            plot = PlotData(project_id=p.id)
            db.session.add(plot)
            
        # Update dimensions from form
        for field in ['area', 'frontage', 'depth', 'front_setback', 'rear_setback', 'side_setback', 'road_width', 'height']:
            val = request.form.get(field)
            if val is not None and val.strip() != '':
                setattr(plot, field, float(val))
            else:
                setattr(plot, field, None)
                
        from app.tnpcr import compute_compliance, fuzzy_confidence
        import json
        compliance_result = compute_compliance(plot)
        plot.compliance_json = json.dumps(compliance_result)
        plot.fuzzy_score = fuzzy_confidence(compliance_result)
        
        # Audit Log
        from app.models import AuditLog
        log = AuditLog(project_id=p.id, actor_id=current_user.id, action='UPDATE', 
                       description='Updated plot dimensions')
        db.session.add(log)
            
        db.session.commit()
        from flask import flash
        flash('Saved.', 'success')
        return redirect(request.referrer or '/dashboard')
    except ValueError as e:
        db.session.rollback()
        from flask import flash
        flash(f'Invalid input: {e}', 'error')
        return redirect(request.referrer or '/dashboard')
    except Exception as e:
        db.session.rollback()
        from flask import current_app, flash
        current_app.logger.error(f'Error in {request.endpoint}: {e}')
        flash('Something went wrong. Please try again.', 'error')
        return redirect(request.referrer or '/dashboard')

@bp.route('/projects/<int:project_id>/plot/confirm', methods=['POST'])
@login_required
def confirm(project_id):
    require_architect()
    try:
        p = Project.query.get_or_404(project_id)
        if current_user.role == 'architect' and p.architect_id != current_user.id:
            abort(403)
            
        if p.plot:
            p.plot.confirmed = True
            p.plot.confirmed_at = utc_now()
            
        db.session.commit()
        from flask import flash
        flash('Saved.', 'success')
        return redirect(request.referrer or '/dashboard')
    except ValueError as e:
        db.session.rollback()
        from flask import flash
        flash(f'Invalid input: {e}', 'error')
        return redirect(request.referrer or '/dashboard')
    except Exception as e:
        db.session.rollback()
        from flask import current_app, flash
        current_app.logger.error(f'Error in {request.endpoint}: {e}')
        flash('Something went wrong. Please try again.', 'error')
        return redirect(request.referrer or '/dashboard')

@bp.route('/api/rag/query', methods=['POST'])
@login_required
def rag_query():
    if current_user.role != 'client': abort(403)
    try:
        data = request.get_json(silent=True) or {}
        question_val = data.get('question') or request.form.get('question') or ''
        question = str(question_val).strip()
        if not question:
            return jsonify({'answer': 'Please enter a question.'}), 400
        from app.rag import query as rag_fn
        ans = rag_fn(question)
        return jsonify({'answer': ans})
    except Exception as e:
        db.session.rollback()
        from flask import current_app
        current_app.logger.error(f'RAG error: {e}')
        return jsonify({'answer': 'Could not process your question. Please try again.', 'error': str(e)}), 500
