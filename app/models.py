from app import db
from app.time_utils import ensure_utc, utc_now
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

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
    id          = db.Column(db.Integer, primary_key=True)
    project_id  = db.Column(db.Integer, db.ForeignKey('project.id'))
    filename    = db.Column(db.String(255), nullable=False)
    caption     = db.Column(db.Text)
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at  = db.Column(db.DateTime, default=utc_now)

    # Linked metadata from pipelines
    requirement_card = db.relationship('RequirementCard', backref='reference', uselist=False, cascade='all, delete-orphan')

class RequirementCard(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    visual_reference_id = db.Column(db.Integer, db.ForeignKey('visual_reference.id'))
    
    # Vision Data (CLIP)
    vision_tags_json    = db.Column(db.Text)  # List of tags from vision.py
    
    # NLP Data (RAG)
    extracted_intent    = db.Column(db.Text)  # Intent extracted from caption
    
    # Fusion Logic
    fused_label         = db.Column(db.String(100))
    divergence_score     = db.Column(db.Float)  # 0-1 (Low is good)
    
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
    original_name   = db.Column(db.String(255))
    category        = db.Column(db.String(50))  # Site Plan|Structural|Legal|Client Docs
    uploader_id     = db.Column(db.Integer, db.ForeignKey('user.id'))
    uploader_role   = db.Column(db.String(10))  # 'architect' | 'client'
    shared_with_client = db.Column(db.Boolean, default=False)
    visible_to_client  = db.Column(db.Boolean, default=False)  # controls client portal visibility
    approval_status = db.Column(db.String(20), default='none')  # none|pending|approved|commented
    approved_at     = db.Column(db.DateTime)
    approval_comment= db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=utc_now)

    uploader        = db.relationship('User', foreign_keys=[uploader_id])
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
    label      = db.Column(db.String(100))
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

COMPLIANCE_ITEMS = [
    ('patta',      'Patta'),
    ('ec',         'EC (Encumbrance Certificate)'),
    ('site_plan',  'Signed Site Plan'),
    ('scheme',     'Scheme Drawings'),
    ('self_cert',  'Self-Certification Form'),
    ('id_proof',   'ID Proof'),
    ('noc',        'NOC (if applicable)'),
]

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
