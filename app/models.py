from app import db
from app.time_utils import ensure_utc, utc_now
from app.compliance_catalog import default_compliance_payload, normalize_doc_type
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Index

class User(db.Model, UserMixin):
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120), nullable=False)
    email           = db.Column(db.String(120), unique=True, nullable=False)
    password_hash   = db.Column(db.String(256), nullable=False)
    role            = db.Column(db.String(20), nullable=False)  # 'architect' | 'client'
    phone           = db.Column(db.String(20))
    email_verified  = db.Column(db.Boolean, default=False)
    reset_token     = db.Column(db.String(100), unique=True, nullable=True)
    reset_token_created_at = db.Column(db.DateTime, nullable=True)
    failed_attempts = db.Column(db.Integer, default=0)
    lock_until      = db.Column(db.DateTime, nullable=True)
    reminders_email = db.Column(db.Boolean, default=True)
    reminders_sms   = db.Column(db.Boolean, default=False)
    created_at      = db.Column(db.DateTime, default=utc_now)

    notifications   = db.relationship('Notification', backref='user', lazy='dynamic')

    # Helper
    @property
    def initials(self):
        parts = self.name.split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else '')).upper()

    @property
    def is_locked(self):
        return bool(self.lock_until and ensure_utc(self.lock_until) > ensure_utc(utc_now()))

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class VisualReference(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    project_id    = db.Column(db.Integer, db.ForeignKey('project.id'))
    filename      = db.Column(db.String(255), nullable=False)
    caption       = db.Column(db.Text)
    uploader_id   = db.Column(db.Integer, db.ForeignKey('user.id'))
    uploader_role = db.Column(db.String(20))          # 'architect' | 'client'
    source_url    = db.Column(db.Text)
    # CLIP pipeline output
    tags_json     = db.Column(db.Text)                # JSON list of top-5 style labels
    style_primary = db.Column(db.String(100))
    colors_json   = db.Column(db.Text)                # JSON list of hex colors
    clip_scores   = db.Column(db.Text)                # full scores JSON
    created_at    = db.Column(db.DateTime, default=utc_now)

class RequirementCard(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    visual_reference_id = db.Column(db.Integer, db.ForeignKey('visual_reference.id'))
    # Project-level aggregated card (one per project)
    project_id          = db.Column(db.Integer, db.ForeignKey('project.id'))

    # Vision Data (CLIP)
    vision_tags_json    = db.Column(db.Text)
    # NLP Data (RAG)
    extracted_intent    = db.Column(db.Text)
    # Fusion (legacy per-ref)
    fused_label         = db.Column(db.String(100))

    # Project-level fusion fields
    visual_style        = db.Column(db.String(200))
    materials_json      = db.Column(db.Text)
    spatial_tags        = db.Column(db.Text)          # JSON list of spatial features
    nlp_intent          = db.Column(db.Text)          # JSON list of intent summaries
    conflicts_json      = db.Column(db.Text)          # JSON list of compliance conflicts
    divergence_score    = db.Column(db.Float)         # 0–100 % mismatch
    feasibility_pct     = db.Column(db.Float)         # 0–100
    generated_at        = db.Column(db.DateTime)

    created_at          = db.Column(db.DateTime, default=utc_now)

class Project(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(200), nullable=False)
    status       = db.Column(db.String(30), default='Design')
    # status values: Design | Review | Submitted | Approved
    plot_zone    = db.Column(db.String(50))
    architect_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    client_id    = db.Column(db.Integer, db.ForeignKey('user.id'))
    total_budget     = db.Column(db.Float, default=0.0)
    allow_client_compliance = db.Column(db.Boolean, default=False)
    created_at   = db.Column(db.DateTime, default=utc_now)
    updated_at   = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    architect    = db.relationship('User', foreign_keys=[architect_id])
    client       = db.relationship('User', foreign_keys=[client_id])
    plot         = db.relationship('PlotData', backref='project', uselist=False, cascade='all, delete-orphan')
    documents    = db.relationship('Document', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    meetings     = db.relationship('Meeting', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    payments     = db.relationship('Payment', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    payment_logs = db.relationship('PaymentLog', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    checklist    = db.relationship('ComplianceItem', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    audit_logs   = db.relationship('AuditLog', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    images       = db.relationship('ProjectImage', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    references    = db.relationship('VisualReference', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    requirements  = db.relationship('Requirement', backref='project', lazy='dynamic', cascade='all, delete-orphan')

    STATUS_ORDER = ['Design', 'Review', 'Submitted', 'Approved']

    @property
    def stage_index(self):
        try: return self.STATUS_ORDER.index(self.status)
        except ValueError: return 0

class PlotData(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    project_id     = db.Column(db.Integer, db.ForeignKey('project.id'), unique=True)
    # Sketch
    sketch_filename= db.Column(db.String(255))
    sketch_uploader= db.Column(db.String(10))  # 'architect' | 'client'
    # Dimensions (architect edits / confirms)
    area           = db.Column(db.Float)
    frontage       = db.Column(db.Float)
    depth          = db.Column(db.Float)
    road_width     = db.Column(db.Float)
    front_setback  = db.Column(db.Float)
    rear_setback   = db.Column(db.Float)
    side_setback   = db.Column(db.Float)
    height         = db.Column(db.Float)
    built_up_area  = db.Column(db.Float)
    # OCR confidence per field (0.0–1.0, -1 = manually entered)
    conf_area      = db.Column(db.Float, default=-1)
    conf_frontage  = db.Column(db.Float, default=-1)
    conf_depth     = db.Column(db.Float, default=-1)
    conf_setback   = db.Column(db.Float, default=-1)
    # Confirmation gate
    confirmed      = db.Column(db.Boolean, default=False)
    confirmed_at   = db.Column(db.DateTime)
    # Fuzzy compliance (computed, stored as JSON string)
    compliance_json= db.Column(db.Text)   # {"area":"pass","frontage":"warn","setback":"fail",...}
    fuzzy_score    = db.Column(db.Float, nullable=True)  # 0–100 computed confidence score
    # Track detection
    track          = db.Column(db.String(2))  # 'A' or 'B'
    updated_at     = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

class Document(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    project_id      = db.Column(db.Integer, db.ForeignKey('project.id'))
    meeting_id      = db.Column(db.Integer, db.ForeignKey('meeting.id'), nullable=True)
    requirement_id  = db.Column(db.Integer, db.ForeignKey('requirement.id'), nullable=True)
    filename        = db.Column(db.String(255), nullable=False)
    file_path       = db.Column(db.String(255))
    doc_type        = db.Column(db.String(50))
    original_name   = db.Column(db.String(255))
    category        = db.Column(db.String(50))  # Site Plan|Structural|Legal|Client Docs
    uploader_id     = db.Column(db.Integer, db.ForeignKey('user.id'))
    uploaded_by     = db.Column(db.Integer, db.ForeignKey('user.id'))
    uploader_role   = db.Column(db.String(10))  # 'architect' | 'client'
    source_module   = db.Column(db.String(50), default='vault')
    # 'vault'|'compliance'|'plot_analysis'|'meetings'|'payments'
    extracted_data  = db.Column(db.Text)
    confidence_score= db.Column(db.Float)
    shared_with_client = db.Column(db.Boolean, default=False)
    visible_to_client  = db.Column(db.Boolean, default=False)  # controls client portal visibility
    approval_status = db.Column(db.String(20), default='none')  # none|pending|approved|commented
    approved_at     = db.Column(db.DateTime)
    approval_comment= db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=utc_now)
    # ── Document-as-source-of-truth additions ───────────────────
    pending_verification = db.Column(db.Boolean, default=False)
    # TRUE when client uploads; cleared when architect verifies
    verified_by     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    verified_at     = db.Column(db.DateTime, nullable=True)
    compliance_doc_type = db.Column(db.String(50), nullable=True)
    # mirrors ComplianceItem.doc_type when source_module='compliance'

    uploader        = db.relationship('User', foreign_keys=[uploader_id])
    uploaded_user   = db.relationship('User', foreign_keys=[uploaded_by])
    verifier        = db.relationship('User', foreign_keys=[verified_by])
    meeting         = db.relationship('Meeting', foreign_keys=[meeting_id], backref='documents')
    requirement     = db.relationship('Requirement', foreign_keys=[requirement_id], backref='documents')
    versions        = db.relationship('DocumentVersion', backref='document',
                                      order_by='DocumentVersion.created_at',
                                      lazy='dynamic', cascade='all, delete-orphan')

    @property
    def current_version(self):
        # order_by(False) clears the relationship's default ordering before applying ours
        return self.versions.order_by(False).order_by(DocumentVersion.version_num.desc()).first()

    @property
    def all_versions(self):
        return self.versions.order_by(False).order_by(DocumentVersion.version_num).all()

    @property
    def version_label(self):
        labels = ['Rev A','Rev B','Rev C','Rev D','Rev E','Rev F','Rev G','Rev H']
        count  = self.versions.count()
        return labels[count - 1] if count <= len(labels) else f'Rev {count}'

    @property
    def vault_path(self):
        return self.file_path or self.filename

    @property
    def normalized_doc_type(self):
        if self.doc_type:
            return normalize_doc_type(self.doc_type)
        return normalize_doc_type(self.original_name or self.category or 'document')

class DocumentVersion(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    document_id   = db.Column(db.Integer, db.ForeignKey('document.id'))
    filename      = db.Column(db.String(255))
    version_num   = db.Column(db.Integer)
    version_label = db.Column(db.String(100))  # user-supplied label e.g. "Rev A", "Draft 2"
    file_type     = db.Column(db.String(100))  # MIME type, e.g. application/pdf
    file_size     = db.Column(db.String(50))   # human-readable, e.g. "42 KB"
    uploader_id   = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at    = db.Column(db.DateTime, default=utc_now)

    uploader      = db.relationship('User')

    VERSION_LABELS = ['Rev A','Rev B','Rev C','Rev D','Rev E','Rev F','Rev G','Rev H']

    @property
    def label(self):
        if self.version_label:
            return self.version_label
        idx = self.version_num - 1
        return self.VERSION_LABELS[idx] if idx < len(self.VERSION_LABELS) else f'Rev {self.version_num}'

class ComplianceItem(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    item       = db.Column(db.String(100))  # patta|ec|site_plan|scheme|self_cert|id_proof|noc
    doc_type   = db.Column(db.String(50))
    label      = db.Column(db.String(100))
    category   = db.Column(db.String(50))
    required   = db.Column(db.Boolean, default=True)
    status     = db.Column(db.String(20), default='missing')
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'))
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    checked    = db.Column(db.Boolean, default=False)
    checked_at = db.Column(db.DateTime)
    checked_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    # Dual-role checkbox support + custom item fields
    arch_checked      = db.Column(db.Boolean, default=False, nullable=True)
    client_checked    = db.Column(db.Boolean, default=False, nullable=True)
    arch_checked_at   = db.Column(db.DateTime, nullable=True)
    client_checked_at = db.Column(db.DateTime, nullable=True)
    is_custom         = db.Column(db.Boolean, default=False, nullable=True)
    description       = db.Column(db.Text, nullable=True)
    custom_label      = db.Column(db.String(200), nullable=True)
    added_by          = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    added_at          = db.Column(db.DateTime, nullable=True)

    document = db.relationship('Document', foreign_keys=[document_id])

    @property
    def status_tone(self):
        return {
            'missing': 'red',
            'optional': 'yellow',
            'uploaded': 'green',
            'verified': 'blue',
        }.get(self.status or 'missing', 'red')

    @property
    def display_status(self):
        return {
            'missing': 'Missing',
            'optional': 'Optional',
            'uploaded': 'Uploaded',
            'verified': 'Verified',
        }.get(self.status or 'missing', 'Missing')

    @property
    def importance_label(self):
        if self.required:
            return 'Required'
        return 'Optional'

COMPLIANCE_ITEMS = [
    (item['doc_type'], item['label'])
    for item in default_compliance_payload()
]


class DocumentAuditLog(db.Model):
    __tablename__ = 'document_audit_log'
    id               = db.Column(db.Integer, primary_key=True)
    document_id      = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=True)
    project_id       = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True)
    action           = db.Column(db.String(30))
    # 'uploaded'|'replaced'|'verified'|'deleted'|'visibility_toggled'
    performed_by     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    performed_by_role = db.Column(db.String(20))
    source_module    = db.Column(db.String(30))
    timestamp        = db.Column(db.DateTime, default=utc_now)
    notes            = db.Column(db.Text, nullable=True)

    performer = db.relationship('User', foreign_keys=[performed_by])

class Meeting(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    project_id      = db.Column(db.Integer, db.ForeignKey('project.id'))
    status          = db.Column(db.String(20), default='awaiting_client')
    # requested | awaiting_client | countered | confirmed | cancelled
    description     = db.Column(db.Text, nullable=True)  # client request reason
    slot_1          = db.Column(db.DateTime)
    slot_2          = db.Column(db.DateTime)
    slot_3          = db.Column(db.DateTime)
    confirmed_slot  = db.Column(db.DateTime)
    confirmed_at    = db.Column(db.DateTime)
    counter_count   = db.Column(db.Integer, default=0)  # max 2 counters allowed
    counter_slot    = db.Column(db.DateTime, nullable=True)  # client's proposed alternative
    mom_discussion  = db.Column(db.Text)
    mom_decision    = db.Column(db.Text)
    mom_client_notes= db.Column(db.Text)
    mom_content     = db.Column(db.Text, nullable=True)      # combined MOM text
    mom_date        = db.Column(db.DateTime, nullable=True)   # when MOM was logged
    outcome         = db.Column(db.String(255), nullable=True)
    completed_at    = db.Column(db.DateTime, nullable=True)
    created_at      = db.Column(db.DateTime, default=utc_now)

    action_items    = db.relationship('ActionItem', backref='meeting', lazy='dynamic')
    reminders       = db.relationship('MeetingReminder', backref='meeting', lazy='dynamic')
    comments        = db.relationship('Comment', backref='meeting', lazy='dynamic',
                                      order_by='Comment.created_at', cascade='all, delete-orphan')
    logs            = db.relationship('MeetingLog', backref='meeting', lazy='dynamic',
                                      order_by='MeetingLog.created_at', cascade='all, delete-orphan')
    request_record  = db.relationship('MeetingRequest', backref='meeting', uselist=False,
                                      cascade='all, delete-orphan')

    STATUS_ALIASES = {
        'awaiting_client': 'proposed',
        'countered': 'counter_proposed',
    }
    STATUS_LABELS = {
        'requested': 'Pending Request',
        'proposed': 'Proposed',
        'counter_proposed': 'Counter Proposed',
        'confirmed': 'Confirmed',
        'completed': 'Completed',
        'cancelled': 'Cancelled',
    }

    @property
    def normalized_status(self):
        return self.STATUS_ALIASES.get(self.status, self.status)

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.normalized_status, self.normalized_status.replace('_', ' ').title())

    @property
    def requested_description(self):
        if self.request_record and self.request_record.description:
            return self.request_record.description
        return self.description

    @property
    def proposed_slots(self):
        return [slot for slot in [self.slot_1, self.slot_2, self.slot_3] if slot]

    @property
    def selected_time(self):
        return self.confirmed_slot or self.counter_slot or self.slot_1

    @property
    def client_name(self):
        return self.project.client.name if self.project and self.project.client else 'Client'


class MeetingRequest(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    meeting_id   = db.Column(db.Integer, db.ForeignKey('meeting.id'), unique=True, nullable=False)
    project_id   = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    description  = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=utc_now)

    project      = db.relationship('Project', foreign_keys=[project_id])
    requester    = db.relationship('User', foreign_keys=[requester_id])

class ActionItem(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    meeting_id   = db.Column(db.Integer, db.ForeignKey('meeting.id'))
    project_id   = db.Column(db.Integer, db.ForeignKey('project.id'))
    description  = db.Column(db.Text, nullable=False)
    assigned_to  = db.Column(db.Integer, db.ForeignKey('user.id'))
    due_date     = db.Column(db.Date)
    status       = db.Column(db.String(20), default='pending')  # pending | complete
    created_at   = db.Column(db.DateTime, default=utc_now)

    assignee     = db.relationship('User')

class MeetingReminder(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    meeting_id   = db.Column(db.Integer, db.ForeignKey('meeting.id'))
    trigger_at   = db.Column(db.DateTime)
    sent         = db.Column(db.Boolean, default=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    recipient    = db.relationship('User')

class Payment(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    amount     = db.Column(db.Float, nullable=False)
    date       = db.Column(db.Date, nullable=False)
    purpose    = db.Column(db.String(50))  # advance | milestone | final
    bill_filename = db.Column(db.String(255))
    notes      = db.Column(db.Text)
    logged_by  = db.Column(db.Integer, db.ForeignKey('user.id'))
    logged_by_role = db.Column(db.String(10), nullable=True, default='architect')  # 'architect' | 'client'
    created_at = db.Column(db.DateTime, default=utc_now)


class PaymentLog(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    project_id     = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    amount         = db.Column(db.Float, nullable=False)
    paid_by        = db.Column(db.String(20), nullable=False)   # client | architect
    paid_to        = db.Column(db.String(30), nullable=False)   # architect | vendor | other
    description    = db.Column(db.Text, nullable=False)
    category       = db.Column(db.String(50), nullable=False)
    stage          = db.Column(db.String(30), nullable=False)
    payment_method = db.Column(db.String(30), nullable=False)
    proof_path     = db.Column(db.String(255), nullable=False)
    status         = db.Column(db.String(30), nullable=False, default='pending')
    comment        = db.Column(db.Text)
    created_by     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    approved_by    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at     = db.Column(db.DateTime, default=utc_now, nullable=False)
    approved_at    = db.Column(db.DateTime, nullable=True)

    creator        = db.relationship('User', foreign_keys=[created_by])
    approver       = db.relationship('User', foreign_keys=[approved_by])

    CATEGORY_CHOICES = [
        'Structural',
        'Electrical',
        'Plumbing',
        'Interior',
        'Approvals',
        'Labour',
        'Material',
        'Architect Fee',
        'Others',
    ]
    STAGE_CHOICES = ['Concept', 'Design', 'Construction', 'Completion', 'Others']
    METHOD_CHOICES = ['UPI', 'GPay', 'Bank Transfer', 'Cash', 'Cheque', 'Other']
    STATUS_CHOICES = ['pending', 'confirmed', 'auto_confirmed', 'rejected']

    @property
    def status_label(self):
        return self.status.replace('_', ' ').title()

    @property
    def proof_filename(self):
        return (self.proof_path or '').split('/')[-1]

class AuditLog(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    actor_id   = db.Column(db.Integer, db.ForeignKey('user.id'))
    action     = db.Column(db.String(50))   # UPLOAD|UPDATE|DELETE|SHARE|APPROVE|LOGIN|CONFIRM
    description= db.Column(db.Text)
    is_client_visible = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now)

    actor      = db.relationship('User')

class ProjectImage(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    project_id  = db.Column(db.Integer, db.ForeignKey('project.id'))
    meeting_id  = db.Column(db.Integer, db.ForeignKey('meeting.id'), nullable=True)
    requirement_id = db.Column(db.Integer, db.ForeignKey('requirement.id'), nullable=True)
    filename    = db.Column(db.String(255))
    tag         = db.Column(db.String(50))  # 3D Render | Drawing | Reference
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at  = db.Column(db.DateTime, default=utc_now)

    uploader    = db.relationship('User')
    meeting     = db.relationship('Meeting', foreign_keys=[meeting_id], backref='images')
    requirement = db.relationship('Requirement', foreign_keys=[requirement_id], backref='images')

class Comment(db.Model):
    """Discussion thread item attached to a meeting."""
    id             = db.Column(db.Integer, primary_key=True)
    meeting_id     = db.Column(db.Integer, db.ForeignKey('meeting.id'), nullable=False)
    user_id        = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    parent_id      = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    content        = db.Column(db.Text, nullable=False)
    requirement_id = db.Column(db.Integer, db.ForeignKey('requirement.id'), nullable=True)
    created_at     = db.Column(db.DateTime, default=utc_now)

    author         = db.relationship('User', foreign_keys=[user_id])
    requirement    = db.relationship('Requirement', foreign_keys=[requirement_id], back_populates='source_comment')
    replies        = db.relationship('Comment',
                                     backref=db.backref('parent', remote_side=[id]),
                                     lazy='dynamic', cascade='all, delete-orphan')


class Requirement(db.Model):
    """Structured requirement, optionally derived from a Comment."""
    id             = db.Column(db.Integer, primary_key=True)
    project_id     = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    meeting_id     = db.Column(db.Integer, db.ForeignKey('meeting.id'), nullable=True)
    title          = db.Column(db.String(255), nullable=False)
    description    = db.Column(db.Text)
    category       = db.Column(db.String(100), default='General')
    # Kanban states: new | confirmed | in_progress | done
    status         = db.Column(db.String(30), default='new')
    created_by     = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at     = db.Column(db.DateTime, default=utc_now)

    creator        = db.relationship('User', foreign_keys=[created_by])
    source_comment = db.relationship('Comment', back_populates='requirement', uselist=False)

    KANBAN_STATES  = ['new', 'confirmed', 'in_progress', 'done']


class MeetingLog(db.Model):
    """Audit trail for meeting state transitions and key actions."""
    id         = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey('meeting.id'), nullable=False)
    actor_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action     = db.Column(db.String(50))  # REQUESTED|PROPOSED|CONFIRMED|COUNTERED|MOM_SAVED|COMMENT
    note       = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utc_now)

    actor      = db.relationship('User', foreign_keys=[actor_id])


class Notification(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'))
    title      = db.Column(db.String(120))
    body       = db.Column(db.Text)
    read       = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now)


# ─── Plot Analysis Module ──────────────────────────────────────────────────

class PlotAnalysis(db.Model):
    __tablename__ = 'plot_analysis'
    __table_args__ = (
        Index('idx_plot_analysis_project_id', 'project_id'),
    )
    id                      = db.Column(db.Integer, primary_key=True)
    project_id              = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    uploaded_by             = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    file_path               = db.Column(db.String(255))    # stored filename (secure)
    original_filename       = db.Column(db.String(255))    # original upload name
    raw_text                = db.Column(db.Text)           # verbatim OCR output
    processed_json          = db.Column(db.Text)           # structured extraction JSON
    input_payload           = db.Column(db.Text)           # manual engine input JSON
    result_payload          = db.Column(db.Text)           # latest engine output JSON
    overall_score           = db.Column(db.Float)          # 0–100 composite compliance
    data_completeness_score = db.Column(db.Float)          # % of fields that had a value
    trust_level             = db.Column(db.String(20))     # INSUFFICIENT | PARTIAL | PROVISIONAL | FULL
    # status: pending → extracted (OCR done) → insufficient_data/analyzed → confirmed
    status                  = db.Column(db.String(20), default='pending')
    # Manual context inputs (set via /manual route)
    road_direction          = db.Column(db.String(10))   # N|S|E|W|NE|NW|SE|SW
    authority               = db.Column(db.String(200))  # CMDA|DTCP|Panchayat|Other
    created_at              = db.Column(db.DateTime, default=utc_now)
    updated_at              = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    uploader   = db.relationship('User', foreign_keys=[uploaded_by])
    project    = db.relationship(
        'Project',
        foreign_keys=[project_id],
        backref=db.backref('plot_analyses', lazy='dynamic', cascade='all, delete-orphan'),
    )
    fields     = db.relationship('PlotExtractedData', backref='analysis',
                                 cascade='all, delete-orphan', lazy='dynamic')
    results    = db.relationship('PlotComplianceResult', backref='analysis',
                                 cascade='all, delete-orphan', lazy='dynamic')
    versions   = db.relationship('PlotAnalysisVersion', backref='analysis',
                                 cascade='all, delete-orphan', lazy='dynamic')
    documents  = db.relationship('PlotDocument', backref='analysis',
                                 cascade='all, delete-orphan', lazy='dynamic')
    conflicts  = db.relationship('PlotConflict', backref='analysis',
                                 cascade='all, delete-orphan', lazy='dynamic')

    @property
    def score_status(self):
        if self.overall_score is None:
            return 'pending'
        if (self.trust_level or '').upper() == 'INSUFFICIENT':
            return 'insufficient'
        if self.overall_score >= 85:
            return 'pass'
        if self.overall_score >= 60:
            return 'warning'
        return 'fail'

    @property
    def score_color(self):
        return {
            'pass': '#4ade80',
            'warning': '#f5c842',
            'fail': '#fb7185',
            'pending': '#625f5b',
            'insufficient': '#60a5fa',
        }[self.score_status]

    @property
    def trust_tone(self):
        level = (self.trust_level or '').upper()
        return {
            'INSUFFICIENT': 'danger',
            'PARTIAL': 'warning',
            'PROVISIONAL': 'info',
            'FULL': 'success',
        }.get(level, 'neutral')

    @property
    def report_ready(self):
        return (self.trust_level or '').upper() != 'INSUFFICIENT' and self.overall_score is not None


class PlotExtractedData(db.Model):
    __tablename__ = 'plot_extracted_data'
    __table_args__ = (
        Index('idx_plot_extracted_analysis_id', 'analysis_id'),
    )
    id                 = db.Column(db.Integer, primary_key=True)
    analysis_id        = db.Column(db.Integer, db.ForeignKey('plot_analysis.id'), nullable=False)
    field_name         = db.Column(db.String(100), nullable=False)
    # Value stored for backward compatibility. Prefer normalized_value for
    # rule-engine reads; value may mirror normalized_value when no separate raw
    # display value is stored.
    value              = db.Column(db.String(255))
    normalized_value   = db.Column(db.String(255))
    unit               = db.Column(db.String(50))    # rule-engine unit: 'sq ft', 'ft', 'm'
    # Original value + unit as detected from the drawing (pre-normalization)
    value_original     = db.Column(db.String(255))   # raw number string from OCR
    unit_original      = db.Column(db.String(50))    # detected unit: 'ft', 'm', 'sq m', etc.
    # Real Tesseract word-level confidence 0.0–1.0 (-1.0 = manually entered)
    confidence         = db.Column(db.Float, default=0.0)
    is_manual_override = db.Column(db.Boolean, default=False)
    # Document source: which document type this value came from
    source             = db.Column(db.String(20))   # 'FMB'|'Patta'|'EC'|'manual'
    document_id        = db.Column(db.Integer, db.ForeignKey('plot_document.id'), nullable=True)

    @property
    def confidence_label(self):
        """
        Classify OCR confidence for UI display.
        Confidence is real Tesseract word score normalized 0–1.
        -1.0 is the sentinel for manually entered values.
        """
        if self.is_manual_override:
            return 'manual'
        c = self.confidence or 0.0
        if c >= 0.80:
            return 'high'     # green
        if c >= 0.50:
            return 'medium'   # amber
        if c > 0.0:
            return 'low'      # red
        return 'missing'      # grey — not extracted

    @property
    def float_value(self):
        try:
            raw = self.normalized_value if self.normalized_value not in (None, '') else self.value
            return float(raw) if raw not in (None, '') else None
        except (ValueError, TypeError):
            return None

    @property
    def raw_float_value(self):
        try:
            return float(self.value) if self.value not in (None, '') else None
        except (ValueError, TypeError):
            return None


class PlotComplianceResult(db.Model):
    __tablename__ = 'plot_compliance_result'
    __table_args__ = (
        Index('idx_plot_compliance_analysis_id', 'analysis_id'),
    )
    id             = db.Column(db.Integer, primary_key=True)
    analysis_id    = db.Column(db.Integer, db.ForeignKey('plot_analysis.id'), nullable=False)
    rule_name      = db.Column(db.String(100), nullable=False)
    rule_label     = db.Column(db.String(200))
    expected_value = db.Column(db.String(100))
    actual_value   = db.Column(db.String(100))
    score          = db.Column(db.Float)   # 0–100 or NULL when not_available
    status         = db.Column(db.String(20))   # pass | warning | fail | not_available
    suggestion     = db.Column(db.Text)


class PlotAnalysisVersion(db.Model):
    __tablename__ = 'plot_analysis_version'
    id            = db.Column(db.Integer, primary_key=True)
    analysis_id   = db.Column(db.Integer, db.ForeignKey('plot_analysis.id'), nullable=False)
    project_id    = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    version_name  = db.Column(db.String(100), nullable=False)
    snapshot_json = db.Column(db.Text)   # complete snapshot of fields + results at save time
    created_by    = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at    = db.Column(db.DateTime, default=utc_now)

    creator = db.relationship('User', foreign_keys=[created_by])


class PlotDocument(db.Model):
    """
    One physical document per analysis (FMB sketch, Patta, EC).
    Each document has its own OCR extraction stored in PlotExtractedData
    with source = doc_type.
    """
    __tablename__ = 'plot_document'
    __table_args__ = (
        Index('idx_plot_document_analysis_id', 'analysis_id'),
    )
    id                = db.Column(db.Integer, primary_key=True)
    analysis_id       = db.Column(db.Integer, db.ForeignKey('plot_analysis.id'), nullable=False)
    doc_type          = db.Column(db.String(20), nullable=False)  # 'FMB'|'Patta'|'EC'
    file_path         = db.Column(db.String(255))
    original_filename = db.Column(db.String(255))
    raw_text          = db.Column(db.Text)    # verbatim OCR text from this document
    status            = db.Column(db.String(20), default='uploaded')  # uploaded|extracted|failed
    uploaded_by       = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at        = db.Column(db.DateTime, default=utc_now)
    # Bridge to central Document vault
    document_id       = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=True)

    uploader = db.relationship('User', foreign_keys=[uploaded_by])
    document = db.relationship('Document', foreign_keys=[document_id])

    @property
    def field_count(self):
        """How many fields were successfully extracted from this document."""
        from app import db as _db
        return (_db.session.query(PlotExtractedData)
                .filter_by(document_id=self.id)
                .filter(PlotExtractedData.value.isnot(None))
                .count())


class PlotConflict(db.Model):
    """
    A detected conflict between values for the same field from different
    document sources (e.g. FMB area=1400 vs Patta area=1350 → 3.6% mismatch).
    Resolved by architect selecting a source of truth or entering custom value.
    """
    __tablename__ = 'plot_conflict'
    __table_args__ = (
        Index('idx_plot_conflict_analysis_id', 'analysis_id'),
    )
    id              = db.Column(db.Integer, primary_key=True)
    analysis_id     = db.Column(db.Integer, db.ForeignKey('plot_analysis.id'), nullable=False)
    field_name      = db.Column(db.String(100), nullable=False)
    values_json     = db.Column(db.Text)    # JSON: {'FMB': 1400.0, 'Patta': 1350.0}
    conflict_type   = db.Column(db.String(50))   # 'mismatch' | 'missing'
    mismatch_pct    = db.Column(db.Float)         # % difference between max and min
    suggested       = db.Column(db.String(50))    # suggested source ('FMB'/'Patta'/etc.)
    resolved        = db.Column(db.Boolean, default=False)
    resolved_source = db.Column(db.String(50))    # 'FMB'|'Patta'|'EC'|'manual'|'custom'
    resolved_value  = db.Column(db.String(255))   # final chosen value (string repr of float)
    created_at      = db.Column(db.DateTime, default=utc_now)
