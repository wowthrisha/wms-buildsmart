from contextlib import contextmanager
from flask import Blueprint, request, redirect, render_template, abort, jsonify, flash, url_for
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from app import db
from app.models import Project, Document, DocumentVersion, User, AuditLog, Meeting, MeetingLog, Requirement
from app.auth import require_architect, roles_required
from app.notifications_service import notify_user
from app.time_utils import utc_now
import mimetypes

bp = Blueprint('documents', __name__)

ALLOWED_DOC_EXT = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'jpg', 'jpeg', 'png', 'dwg', 'dxf'}

DOC_CATEGORIES = ['Site Plan', 'Structural', 'Legal', 'Client Docs', 'Permit', 'Others']


@contextmanager
def _transaction():
    try:
        with db.session.begin_nested():
            yield
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _docs_redirect(project_id):
    """Redirect back to the project workspace on the documents tab."""
    return redirect(url_for('projects.workspace', project_id=project_id) + '?tab=documents')


def _post_upload_redirect(project_id, meeting_id=None):
    if meeting_id and request.form.get('next') == 'mom':
        return redirect(url_for('meetings.mom_workspace', meeting_id=meeting_id))
    return _docs_redirect(project_id)


def _get_file_size_str(file_obj):
    """Return human-readable file size by seeking to end."""
    try:
        file_obj.seek(0, 2)
        size = file_obj.tell()
        file_obj.seek(0)
        if size < 1024:
            return f'{size} B'
        elif size < 1024 * 1024:
            return f'{size // 1024} KB'
        else:
            return f'{size / (1024 * 1024):.1f} MB'
    except Exception:
        return 'unknown'


@bp.route('/documents')
@login_required
def index():
    require_architect()
    docs = Document.query.options(joinedload(Document.project)).join(Project).filter(Project.architect_id == current_user.id).all()
    projects = Project.query.options(joinedload(Project.client)).filter_by(architect_id=current_user.id).all()
    return render_template('architect/documents_standalone.html', documents=docs, projects=projects)


@bp.route('/projects/<int:project_id>/documents/upload', methods=['POST'])
@login_required
@roles_required(['architect', 'client'])
def upload(project_id):
    saved_filename = None
    try:
        p = Project.query.get_or_404(project_id)
        if current_user.role == 'architect' and p.architect_id != current_user.id:
            abort(403)
        if current_user.role == 'client' and p.client_id != current_user.id:
            abort(403)

        file = request.files.get('doc') or request.files.get('file')
        if not (file and file.filename):
            flash('No file provided.', 'error')
            return _post_upload_redirect(project_id, request.form.get('meeting_id', type=int))

        meeting_id = request.form.get('meeting_id', type=int)
        requirement_id = request.form.get('requirement_id', type=int)
        if meeting_id:
            meeting = Meeting.query.get_or_404(meeting_id)
            if meeting.project_id != p.id:
                abort(403)
        else:
            meeting = None
        if requirement_id:
            requirement = Requirement.query.get_or_404(requirement_id)
            if requirement.project_id != p.id:
                abort(403)
        else:
            requirement = None

        from app.storage import delete_file_securely, validate_secure_mime, save_file_securely
        if not validate_secure_mime(file, ['application/pdf', 'image/jpeg', 'image/png']):
            flash('Malicious file signature detected or unsupported file type.', 'error')
            return _post_upload_redirect(project_id, meeting_id)

        # Capture metadata before save_file_securely consumes the stream
        file_size_str = _get_file_size_str(file)
        file_type = mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
        title = request.form.get('title') or file.filename
        version_label = request.form.get('version_label', '').strip() or None

        filename = save_file_securely(file)
        saved_filename = filename

        # ── Versioning: find existing document by title + project ──────────
        doc_query = Document.query.filter_by(project_id=p.id, original_name=title)
        if meeting:
            doc_query = doc_query.filter_by(meeting_id=meeting.id)
        elif requirement:
            doc_query = doc_query.filter_by(requirement_id=requirement.id)
        doc = doc_query.first()

        with _transaction():
            if doc is None:
                doc = Document(
                    project_id=p.id,
                    meeting_id=meeting.id if meeting else None,
                    requirement_id=requirement.id if requirement else None,
                    filename=filename,
                    original_name=title,
                    category=request.form.get('category', 'Site Plan'),
                    uploader_id=current_user.id,
                    uploader_role=current_user.role,
                    visible_to_client=False,
                )
                db.session.add(doc)
                db.session.flush()  # get doc.id before counting versions

            # Always update the main filename pointer to the latest file
            doc.filename = filename
            if meeting and not doc.meeting_id:
                doc.meeting_id = meeting.id
            if requirement and not doc.requirement_id:
                doc.requirement_id = requirement.id

            next_version = doc.versions.count() + 1
            new_v = DocumentVersion(
                document_id=doc.id,
                filename=filename,
                version_num=next_version,
                version_label=version_label,
                file_type=file_type,
                file_size=file_size_str,
                uploader_id=current_user.id,
            )
            db.session.add(new_v)

            log = AuditLog(
                project_id=p.id,
                actor_id=current_user.id,
                action='UPLOAD',
                description=f'Uploaded document "{title}" ({version_label or f"v{next_version}"})',
            )
            db.session.add(log)
            if meeting:
                db.session.add(MeetingLog(
                    meeting_id=meeting.id,
                    actor_id=current_user.id,
                    action='FILE_UPLOADED',
                    note=title[:255],
                ))

        flash('Saved.', 'success')
        return _post_upload_redirect(project_id, meeting_id)

    except ValueError as e:
        db.session.rollback()
        flash(f'Invalid input: {e}', 'error')
        return _post_upload_redirect(project_id, request.form.get('meeting_id', type=int))
    except Exception as e:
        db.session.rollback()
        if saved_filename:
            delete_file_securely(saved_filename)
        from flask import current_app
        current_app.logger.error(f'Error in {request.endpoint}: {e}')
        flash('Something went wrong. Please try again.', 'error')
        return _post_upload_redirect(project_id, request.form.get('meeting_id', type=int))


@bp.route('/documents/<int:doc_id>/toggle-visibility', methods=['POST'])
@login_required
def toggle_visibility(doc_id):
    require_architect()
    d = Document.query.get_or_404(doc_id)
    if d.project.architect_id != current_user.id:
        abort(403)

    with _transaction():
        d.visible_to_client = not d.visible_to_client

    return jsonify({'visible_to_client': d.visible_to_client, 'doc_id': doc_id})


@bp.route('/documents/project/<int:project_id>', methods=['GET'])
@login_required
def list_project_documents(project_id):
    p = Project.query.get_or_404(project_id)
    if current_user.role == 'architect' and p.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client' and p.client_id != current_user.id:
        abort(403)

    docs_query = Document.query.filter_by(project_id=project_id)
    if current_user.role == 'client':
        docs_query = docs_query.filter_by(visible_to_client=True)

    docs = docs_query.all()
    doc_ids = [doc.id for doc in docs]
    versions_by_doc = {}
    if doc_ids:
        version_rows = (
            DocumentVersion.query
            .options(joinedload(DocumentVersion.uploader))
            .filter(DocumentVersion.document_id.in_(doc_ids))
            .order_by(DocumentVersion.document_id, DocumentVersion.version_num)
            .all()
        )
        for version in version_rows:
            versions_by_doc.setdefault(version.document_id, []).append(version)

    result = []
    for d in docs:
        versions = []
        for v in versions_by_doc.get(d.id, []):
            versions.append({
                'version_number': v.version_num,
                'version_label': v.label,
                'filename': v.filename,
                'file_type': v.file_type,
                'file_size': v.file_size,
                'uploaded_by': v.uploader.name if v.uploader else 'Unknown',
                'uploaded_at': v.created_at.isoformat() if v.created_at else None,
            })
        result.append({
            'id': d.id,
            'title': d.original_name,
            'category': d.category,
            'meeting_id': d.meeting_id,
            'requirement_id': d.requirement_id,
            'visible_to_client': d.visible_to_client,
            'created_at': d.created_at.isoformat() if d.created_at else None,
            'versions': versions,
        })

    return jsonify(result)


@bp.route('/documents/<int:doc_id>/share', methods=['POST'])
@login_required
def share(doc_id):
    require_architect()
    try:
        d = Document.query.get_or_404(doc_id)
        p = d.project
        if current_user.role == 'architect' and p.architect_id != current_user.id:
            abort(403)

        with _transaction():
            d.shared_with_client = True
            d.visible_to_client = True

            log = AuditLog(
                project_id=p.id,
                actor_id=current_user.id,
                action='SHARE',
                description=f'Shared document: {d.original_name}',
                is_client_visible=True,
            )
            db.session.add(log)

        try:
            if p.client:
                notify_user(p.client_id, 'Document shared',
                            f'A document has been shared with you on {p.name}.')
        except Exception as notify_e:
            from flask import current_app
            current_app.logger.error(f'Notification error: {notify_e}')

        flash('Saved.', 'success')
        return _docs_redirect(p.id)
    except ValueError as e:
        db.session.rollback()
        flash(f'Invalid input: {e}', 'error')
        return redirect(request.referrer or '/dashboard')
    except Exception as e:
        db.session.rollback()
        from flask import current_app
        current_app.logger.error(f'Error in {request.endpoint}: {e}')
        flash('Something went wrong. Please try again.', 'error')
        return redirect(request.referrer or '/dashboard')


@bp.route('/documents/version/<int:version_id>/download')
@login_required
def download_version(version_id):
    v = DocumentVersion.query.get_or_404(version_id)
    d = v.document
    p = d.project
    if current_user.role == 'architect' and p.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client':
        if p.client_id != current_user.id:
            abort(403)
        if not d.visible_to_client:
            abort(403)
    from app.storage import send_file_securely
    return send_file_securely(v.filename)


@bp.route('/documents/<int:doc_id>/download')
@login_required
def download(doc_id):
    d = Document.query.get_or_404(doc_id)
    p = d.project
    if current_user.role == 'architect' and p.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client':
        if p.client_id != current_user.id:
            abort(403)
        if not d.visible_to_client:
            abort(403)

    from app.storage import send_file_securely
    return send_file_securely(d.filename)


@bp.route('/documents/<int:doc_id>/delete', methods=['POST'])
@login_required
def delete(doc_id):
    require_architect()
    d = Document.query.get_or_404(doc_id)
    project_id = d.project_id
    if d.project.architect_id != current_user.id:
        abort(403)

    log = AuditLog(
        project_id=project_id,
        actor_id=current_user.id,
        action='DELETE',
        description=f'Deleted document: {d.original_name}',
    )
    with _transaction():
        db.session.add(log)
        db.session.delete(d)
    flash('Document deleted.', 'success')
    return _docs_redirect(project_id)


@bp.route('/documents/<int:doc_id>/upload-version', methods=['POST'])
@login_required
def upload_version(doc_id):
    require_architect()
    saved_filename = None
    try:
        d = Document.query.get_or_404(doc_id)
        project_id = d.project.id
        if d.project.architect_id != current_user.id:
            abort(403)

        file = request.files.get('file') or request.files.get('doc')
        if not (file and file.filename):
            flash('No file provided.', 'error')
            return _docs_redirect(project_id)

        from app.storage import delete_file_securely, validate_secure_mime, save_file_securely
        if not validate_secure_mime(file, ['application/pdf', 'image/jpeg', 'image/png']):
            flash('Malicious file signature detected.', 'error')
            return _docs_redirect(project_id)

        file_size_str = _get_file_size_str(file)
        file_type = mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
        version_label = request.form.get('version_label', '').strip() or None
        filename = save_file_securely(file)
        saved_filename = filename

        with _transaction():
            new_v = DocumentVersion(
                document_id=d.id,
                filename=filename,
                version_num=d.versions.count() + 1,
                version_label=version_label,
                file_type=file_type,
                file_size=file_size_str,
                uploader_id=current_user.id,
            )
            db.session.add(new_v)
            d.filename = filename
    except Exception:
        if saved_filename:
            delete_file_securely(saved_filename)
        flash('Unable to upload the new document version right now.', 'error')
        return _docs_redirect(d.project.id if 'd' in locals() else request.form.get('project_id', type=int) or 0)
    flash('New version uploaded.', 'success')
    return _docs_redirect(project_id)


@bp.route('/documents/<int:doc_id>/request-approval', methods=['POST'])
@login_required
def request_approval(doc_id):
    require_architect()
    d = Document.query.get_or_404(doc_id)
    if d.project.architect_id != current_user.id:
        abort(403)

    with _transaction():
        d.approval_status = 'pending'

    try:
        if d.project.client_id:
            notify_user(d.project.client_id, 'Approval Requested',
                        f'Approval requested for document: {d.original_name}')
    except Exception:
        pass

    flash('Approval requested.', 'success')
    return _docs_redirect(d.project.id)


@bp.route('/documents/<int:doc_id>/approve', methods=['POST'])
@login_required
def approve(doc_id):
    if current_user.role != 'client':
        abort(403)
    d = Document.query.get_or_404(doc_id)
    if d.project.client_id != current_user.id:
        abort(403)
    if not d.visible_to_client:
        abort(403)

    with _transaction():
        d.approval_status = 'approved'
        d.approved_at = utc_now()

    try:
        notify_user(d.project.architect_id, 'Document Approved',
                    f'{current_user.name} approved {d.original_name or d.filename}')
    except Exception as e:
        from flask import current_app
        current_app.logger.error(f'notify_user error: {e}')

    return redirect(url_for('client.documents'))
