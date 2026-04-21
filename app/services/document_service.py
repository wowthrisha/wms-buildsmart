"""
document_service.py — Single source of truth for all document uploads.

Every module (compliance, plot_analysis, vault, meetings, payments) must
call create_or_replace_document() instead of writing to Document directly.
"""

from __future__ import annotations

import mimetypes

from app import db
from app.models import AuditLog, Document, DocumentVersion
from app.storage import save_file_securely, validate_secure_mime
from app.time_utils import utc_now

SOURCE_MODULE_LABELS = {
    'vault':        'General',
    'compliance':   'Compliance',
    'plot_analysis': 'Plot Analysis',
    'meetings':     'MOM / Minutes',
    'payments':     'Payment',
    'documents':    'General',   # legacy alias
}

COMPLIANCE_DOC_DISPLAY_NAMES = {
    'patta':             'Patta / Chitta',
    'ec':                'Encumbrance Certificate',
    'fmb':               'FMB Sketch',
    'sale_deed':         'Sale Deed',
    'layout_approval':   'Layout Approval',
    'road_width':        'Road Width Proof',
    'authority':         'Authority Record',
    'site_measurement':  'Site Measurement',
}


def _file_size_str(file_obj) -> str:
    try:
        file_obj.seek(0, 2)
        size = file_obj.tell()
        file_obj.seek(0)
        if size < 1024:
            return f'{size} B'
        if size < 1024 * 1024:
            return f'{size // 1024} KB'
        return f'{size / (1024 * 1024):.1f} MB'
    except Exception:
        return 'unknown'


def create_or_replace_document(
    file,                        # werkzeug FileStorage
    project_id: int,
    uploaded_by: int,            # user.id
    uploaded_by_role: str,       # 'architect' | 'client'
    source_module: str,          # 'compliance'|'plot_analysis'|'vault'|'meetings'|'payments'
    display_name: str | None = None,
    compliance_doc_type: str | None = None,
    plot_analysis_id: int | None = None,
    doc_type: str | None = None,
    category: str | None = None,
    meeting_id: int | None = None,
    requirement_id: int | None = None,
    version_label: str | None = None,
    allowed_mimes: list[str] | None = None,
    visible_to_client: bool = False,
    existing_document_id: int | None = None,
) -> tuple[Document, bool]:
    """
    Single entry point for all document uploads.
    Returns (document, is_new).

    Rules:
    - If existing_document_id is given → add a new DocumentVersion to that doc.
    - If compliance_doc_type is given → look for an existing Document with that
      doc_type + project to deduplicate (treat as replace if found).
    - Otherwise → create a new Document record.
    - plot_analysis_id is accepted for plot-analysis callers and future linking.
    - Always writes an AuditLog entry with source_module metadata.
    - pending_verification = True when uploaded_by_role == 'client'.
    """
    _ = plot_analysis_id
    source_module = 'vault' if source_module in ('documents', 'vault') else source_module
    # Validate mime
    allowed_mimes = allowed_mimes or ['application/pdf', 'image/jpeg', 'image/png', 'image/webp']
    if not validate_secure_mime(file, allowed_mimes):
        raise ValueError('Unsupported or potentially malicious file type.')

    file_size_str = _file_size_str(file)
    file_type = mimetypes.guess_type(file.filename or '')[0] or 'application/octet-stream'
    original_name = display_name or (file.filename or 'document')
    pending = (uploaded_by_role == 'client')

    # Save file to storage (S3 or local)
    saved_filename = save_file_securely(file)

    # ── Resolve existing document ───────────────────────────────────
    doc: Document | None = None
    is_new = True

    if existing_document_id:
        doc = Document.query.get(existing_document_id)

    if doc is None and compliance_doc_type:
        doc = Document.query.filter_by(
            project_id=project_id,
            compliance_doc_type=compliance_doc_type,
            source_module='compliance',
        ).first()

    if doc is not None:
        # ── Replace: add new version ────────────────────────────────
        is_new = False
        doc.filename   = saved_filename
        doc.file_path  = saved_filename
        doc.uploaded_by   = uploaded_by
        doc.uploader_role = uploaded_by_role
        doc.source_module = source_module
        doc.doc_type = doc_type or compliance_doc_type or doc.doc_type
        doc.original_name = original_name or doc.original_name
        doc.compliance_doc_type = compliance_doc_type or doc.compliance_doc_type
        doc.category = category or doc.category or _default_category(source_module)
        if meeting_id is not None:
            doc.meeting_id = meeting_id
        if requirement_id is not None:
            doc.requirement_id = requirement_id
        doc.visible_to_client = visible_to_client if visible_to_client else doc.visible_to_client
        if pending:
            doc.pending_verification = True
            doc.verified_by  = None
            doc.verified_at  = None
        # Architect replaces → clear any pending flag
        if uploaded_by_role == 'architect':
            doc.pending_verification = False
    else:
        # ── Create new Document ─────────────────────────────────────
        doc = Document(
            project_id=project_id,
            meeting_id=meeting_id,
            requirement_id=requirement_id,
            filename=saved_filename,
            file_path=saved_filename,
            doc_type=doc_type or compliance_doc_type or source_module,
            original_name=original_name,
            category=category or _default_category(source_module),
            uploaded_by=uploaded_by,
            uploader_role=uploaded_by_role,
            source_module=source_module,
            compliance_doc_type=compliance_doc_type,
            visible_to_client=visible_to_client,
            pending_verification=pending,
            approval_status='none',
            created_at=utc_now(),
        )
        db.session.add(doc)
        db.session.flush()   # get doc.id

    # ── New DocumentVersion ─────────────────────────────────────────
    next_ver = db.session.query(DocumentVersion).filter_by(document_id=doc.id).count() + 1
    version = DocumentVersion(
        document_id=doc.id,
        filename=saved_filename,
        version_num=next_ver,
        version_label=version_label,
        file_type=file_type,
        file_size=file_size_str,
        uploader_id=uploaded_by,
        created_at=utc_now(),
    )
    db.session.add(version)

    # ── Audit log ───────────────────────────────────────────────────
    log = AuditLog(
        project_id=project_id,
        actor_id=uploaded_by,
        action='DOCUMENT_UPLOADED' if is_new else 'DOCUMENT_REPLACED',
        description=f'{"Uploaded" if is_new else "Replaced"} document "{doc.original_name}".',
        source_module=source_module,
        is_client_visible=bool(visible_to_client or uploaded_by_role == 'client'),
        created_at=utc_now(),
    )
    db.session.add(log)
    db.session.commit()

    return doc, is_new


def create_vault_record_from_path(
    file_path: str,
    original_filename: str,
    project_id: int,
    uploaded_by: int,
    uploaded_by_role: str,
    source_module: str,
    display_name: str | None = None,
    compliance_doc_type: str | None = None,
    meeting_id: int | None = None,
    requirement_id: int | None = None,
    visible_to_client: bool = False,
    existing_document_id: int | None = None,
) -> tuple[Document, bool]:
    """
    Create (or update) a Document + DocumentVersion record for a file that has
    already been saved to storage.  Use this when the file stream is exhausted.
    Returns (document, is_new).
    """
    import mimetypes as _mt
    source_module = 'vault' if source_module in ('documents', 'vault') else source_module
    pending = (uploaded_by_role == 'client')
    file_type = _mt.guess_type(original_filename or '')[0] or 'application/octet-stream'
    original_name = display_name or original_filename or 'document'

    doc: Document | None = None
    is_new = True

    if existing_document_id:
        doc = Document.query.get(existing_document_id)

    if doc is None and compliance_doc_type:
        doc = Document.query.filter_by(
            project_id=project_id,
            compliance_doc_type=compliance_doc_type,
            source_module=source_module,
        ).first()

    if doc is not None:
        is_new = False
        doc.filename = file_path
        doc.file_path = file_path
        doc.uploaded_by = uploaded_by
        doc.uploader_role = uploaded_by_role
        doc.source_module = source_module
        if compliance_doc_type:
            doc.compliance_doc_type = compliance_doc_type
        if display_name:
            doc.original_name = display_name
        if meeting_id is not None:
            doc.meeting_id = meeting_id
        if requirement_id is not None:
            doc.requirement_id = requirement_id
        if pending:
            doc.pending_verification = True
            doc.verified_by = None
            doc.verified_at = None
        if uploaded_by_role == 'architect':
            doc.pending_verification = False
    else:
        doc = Document(
            project_id=project_id,
            meeting_id=meeting_id,
            requirement_id=requirement_id,
            filename=file_path,
            file_path=file_path,
            doc_type=compliance_doc_type or source_module,
            original_name=original_name,
            category=_default_category(source_module),
            uploaded_by=uploaded_by,
            uploader_role=uploaded_by_role,
            source_module=source_module,
            compliance_doc_type=compliance_doc_type,
            visible_to_client=visible_to_client,
            pending_verification=pending,
            approval_status='none',
            created_at=utc_now(),
        )
        db.session.add(doc)
        db.session.flush()

    next_ver = db.session.query(DocumentVersion).filter_by(document_id=doc.id).count() + 1
    version = DocumentVersion(
        document_id=doc.id,
        filename=file_path,
        version_num=next_ver,
        file_type=file_type,
        file_size='',
        uploader_id=uploaded_by,
        created_at=utc_now(),
    )
    db.session.add(version)

    log = AuditLog(
        project_id=project_id,
        actor_id=uploaded_by,
        action='DOCUMENT_UPLOADED' if is_new else 'DOCUMENT_REPLACED',
        description=f'{"Uploaded" if is_new else "Replaced"} document "{original_name}".',
        source_module=source_module,
        is_client_visible=bool(visible_to_client or uploaded_by_role == 'client'),
        created_at=utc_now(),
    )
    db.session.add(log)
    db.session.commit()
    return doc, is_new


def verify_document(document_id: int, verified_by_user_id: int) -> Document:
    """
    Architect verifies a client-uploaded document.
    Clears pending_verification; writes audit log.
    """
    doc = Document.query.get_or_404(document_id)
    doc.pending_verification = False
    doc.verified_by  = verified_by_user_id
    doc.verified_at  = utc_now()
    doc.approval_status = 'approved'

    log = AuditLog(
        project_id=doc.project_id,
        actor_id=verified_by_user_id,
        action='DOCUMENT_VERIFIED',
        description=f'Document "{doc.original_name}" was verified.',
        source_module=doc.source_module or 'vault',
        is_client_visible=True,
        created_at=utc_now(),
    )
    db.session.add(log)
    db.session.commit()
    return doc


def _default_category(source_module: str) -> str:
    return {
        'compliance':    'Legal',
        'plot_analysis': 'Site Plan',
        'meetings':      'Client Docs',
        'payments':      'Others',
        'vault':         'Others',
        'documents':     'Others',
    }.get(source_module, 'Others')
