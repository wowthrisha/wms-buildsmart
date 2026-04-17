from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app import db
from app.auth import require_architect
from app.models import AuditLog, PaymentLog, Project
from app.notifications_service import notify_user_comms
from app.time_utils import ensure_utc, utc_now

bp = Blueprint('payments', __name__)

CONFIRMED_STATUSES = {'confirmed', 'auto_confirmed'}
PAYMENT_PROOF_MIMES = ['application/pdf', 'image/jpeg', 'image/png']


@contextmanager
def _transaction():
    try:
        with db.session.begin_nested():
            yield
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _user_projects():
    if current_user.role == 'architect':
        return Project.query.options(joinedload(Project.client)).filter_by(architect_id=current_user.id).order_by(Project.updated_at.desc()).all()
    if current_user.role == 'client':
        return Project.query.options(joinedload(Project.architect)).filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).all()
    abort(403)


def _get_project_or_403(project_id):
    project = Project.query.options(joinedload(Project.client), joinedload(Project.architect)).get_or_404(project_id)
    if current_user.role == 'architect' and project.architect_id != current_user.id:
        abort(403)
    if current_user.role == 'client' and project.client_id != current_user.id:
        abort(403)
    return project


def _is_client_payment(log):
    return log.paid_by == 'client'


def _is_architect_expense(log):
    return log.paid_by == 'architect'


def _payment_logs_for(project):
    return project.payment_logs.order_by(PaymentLog.created_at.desc(), PaymentLog.id.desc()).all()


def _append_comment(existing, author, text):
    note = (text or '').strip()
    if not note:
        return existing
    stamp = utc_now().strftime('%d %b %Y · %H:%M')
    line = f'{author} ({stamp}): {note}'
    return f'{existing}\n{line}'.strip() if existing else line


def _notify_counterparty(project, title, body):
    try:
        if current_user.role == 'architect' and project.client:
            notify_user_comms(project.client, title, body)
        elif current_user.role == 'client' and project.architect:
            notify_user_comms(project.architect, title, body)
    except Exception as exc:
        current_app.logger.error(f'Payment notification error: {exc}')


def _auto_confirm_stale(project):
    last_run = ensure_utc(project.auto_confirm_checked_at) if project.auto_confirm_checked_at else None
    now = utc_now()
    if last_run and now - last_run < timedelta(hours=1):
        return 0

    cutoff = utc_now() - timedelta(days=7)
    stale_logs = (
        PaymentLog.query
        .filter_by(project_id=project.id, paid_by='client', status='pending')
        .filter(PaymentLog.created_at <= cutoff)
        .all()
    )

    approved_at = now
    with _transaction():
        for log in stale_logs:
            log.status = 'auto_confirmed'
            log.approved_by = project.architect_id or log.created_by
            log.approved_at = approved_at
            db.session.add(AuditLog(
                project_id=project.id,
                actor_id=project.architect_id or log.created_by,
                action='PAYMENT_AUTO_CONFIRMED',
                description=f'Client payment #{log.id} auto-confirmed after 7 days.',
                is_client_visible=True,
            ))
        project.auto_confirm_checked_at = approved_at
    return len(stale_logs)


def _build_summary(project, logs):
    total_budget = float(project.total_budget or 0)
    paid_to_architect = sum(
        log.amount for log in logs
        if log.status in CONFIRMED_STATUSES and log.paid_to == 'architect'
    )
    spent_on_project = sum(
        log.amount for log in logs
        if _is_architect_expense(log) and log.status != 'rejected'
    )
    used_total = paid_to_architect + spent_on_project
    remaining = total_budget - used_total
    usage_pct = (used_total / total_budget * 100) if total_budget > 0 else 0

    if total_budget > 0 and used_total > total_budget:
        status = {'tone': 'danger', 'label': 'Over Budget'}
    elif total_budget > 0 and usage_pct > 80:
        status = {'tone': 'warning', 'label': 'Slightly Over'}
    else:
        status = {'tone': 'success', 'label': 'On Track'}

    return {
        'total_budget': total_budget,
        'paid_to_architect': paid_to_architect,
        'spent_on_project': spent_on_project,
        'used_total': used_total,
        'remaining': remaining,
        'usage_pct': max(0, min(usage_pct, 100 if total_budget > 0 else 0)),
        'status': status,
    }


def _filter_logs(logs):
    entry_type = request.args.get('entry_type', 'all')
    paid_by = request.args.get('paid_by', 'all')
    category = request.args.get('category', 'all')
    stage = request.args.get('stage', 'all')

    filtered = []
    for log in logs:
        if entry_type == 'client_payments' and not _is_client_payment(log):
            continue
        if entry_type == 'expenses' and not _is_architect_expense(log):
            continue
        if paid_by in ('client', 'architect') and log.paid_by != paid_by:
            continue
        if category != 'all' and log.category != category:
            continue
        if stage != 'all' and log.stage != stage:
            continue
        filtered.append(log)

    return filtered, {
        'entry_type': entry_type,
        'paid_by': paid_by,
        'category': category,
        'stage': stage,
    }


def _category_analytics(logs):
    grouped = defaultdict(lambda: {'amount': 0.0, 'count': 0})
    for log in logs:
        if log.status == 'rejected':
            continue
        grouped[log.category]['amount'] += log.amount
        grouped[log.category]['count'] += 1

    analytics = []
    for name, payload in grouped.items():
        analytics.append({
            'category': name,
            'amount': payload['amount'],
            'count': payload['count'],
        })
    return sorted(analytics, key=lambda item: item['amount'], reverse=True)


def _next_step_payload(logs):
    if current_user.role == 'architect':
        pending = [log for log in logs if _is_client_payment(log) and log.status == 'pending']
        if pending:
            return {
                'tone': 'warning',
                'headline': 'Action needed: Confirm client payment',
                'detail': f'{len(pending)} client payment waiting for approval in the shared ledger.',
            }
    elif current_user.role == 'client':
        expenses = [log for log in logs if _is_architect_expense(log) and log.status != 'rejected']
        if expenses:
            latest = max(expenses, key=lambda log: log.created_at)
            return {
                'tone': 'info',
                'headline': 'New expense logged by architect',
                'detail': f'Latest expense: {latest.description} for ₹{latest.amount:,.0f}.',
            }

    return {
        'tone': 'neutral',
        'headline': 'Ledger is in sync',
        'detail': 'No pending financial actions right now.',
    }


def _build_project_cards(projects):
    cards = []
    for project in projects:
        _auto_confirm_stale(project)
        logs = _payment_logs_for(project)
        summary = _build_summary(project, logs)
        cards.append({
            'project': project,
            'summary': summary,
            'pending_count': sum(1 for log in logs if _is_client_payment(log) and log.status == 'pending'),
            'entry_count': len(logs),
        })
    return cards


def _validate_required_choice(value, choices, label):
    if value not in choices:
        raise ValueError(f'Invalid {label}.')
    return value


@bp.route('/payments')
@login_required
def index():
    if current_user.role == 'client':
        project = Project.query.filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).first()
        if not project:
            return render_template('client/no_project.html')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    require_architect()
    projects = _user_projects()
    return render_template('payments_index.html', project_cards=_build_project_cards(projects))


@bp.route('/projects/<int:project_id>/payments')
@login_required
def project_overview(project_id):
    project = _get_project_or_403(project_id)
    _auto_confirm_stale(project)
    logs = _payment_logs_for(project)
    filtered_logs, filters = _filter_logs(logs)
    summary = _build_summary(project, logs)
    next_step = _next_step_payload(logs)
    analytics = _category_analytics(filtered_logs)
    pending_client_logs = [log for log in logs if _is_client_payment(log) and log.status == 'pending']

    return render_template(
        'payments_project.html',
        project=project,
        active_project=project,
        active_tab='payments',
        logs=filtered_logs,
        all_logs=logs,
        filters=filters,
        summary=summary,
        next_step=next_step,
        analytics=analytics,
        pending_client_logs=pending_client_logs,
        categories=PaymentLog.CATEGORY_CHOICES,
        stages=PaymentLog.STAGE_CHOICES,
        methods=PaymentLog.METHOD_CHOICES,
        chart_values=[summary['paid_to_architect'], summary['spent_on_project']],
    )


@bp.route('/projects/<int:project_id>/payments/budget', methods=['POST'])
@login_required
def update_budget(project_id):
    project = _get_project_or_403(project_id)
    require_architect()
    try:
        total_budget = float(request.form.get('total_budget', 0) or 0)
    except ValueError:
        flash('Please enter a valid budget amount.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    if total_budget < 0:
        flash('Budget cannot be negative.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    with _transaction():
        project.total_budget = total_budget
    flash('Project budget updated.', 'success')
    return redirect(url_for('payments.project_overview', project_id=project.id))




@bp.route('/projects/<int:project_id>/payments/add', methods=['POST'])
@login_required
def add(project_id):
    project = _get_project_or_403(project_id)
    if current_user.role not in ('architect', 'client'):
        abort(403)
    proof_path = None

    amount_raw = (request.form.get('amount') or '').strip()
    description = (request.form.get('description') or '').strip()[:500]
    category = (request.form.get('category') or '').strip()
    stage = (request.form.get('stage') or '').strip()
    payment_method = (request.form.get('payment_method') or '').strip()
    paid_to = (request.form.get('paid_to') or '').strip() or ('architect' if current_user.role == 'client' else 'vendor')
    note = (request.form.get('comment') or '').strip()[:2000]
    proof = request.files.get('proof')

    if not amount_raw or not description or not category or not stage or not payment_method:
        flash('Please fill in every required payment field.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    if not proof or not proof.filename:
        flash('Proof upload is mandatory for every payment entry.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    try:
        amount = float(amount_raw)
    except ValueError:
        flash('Please enter a valid amount.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    if amount <= 0:
        flash('Amount must be greater than zero.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    try:
        category = _validate_required_choice(category, PaymentLog.CATEGORY_CHOICES, 'category')
        stage = _validate_required_choice(stage, PaymentLog.STAGE_CHOICES, 'stage')
        payment_method = _validate_required_choice(payment_method, PaymentLog.METHOD_CHOICES, 'payment method')
        if current_user.role == 'client':
            paid_by = 'client'
            paid_to = 'architect'
        else:
            paid_by = 'architect'
            paid_to = _validate_required_choice(paid_to, ['architect', 'vendor', 'other'], 'paid to value')
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    try:
        from app.storage import delete_file_securely, save_file_securely, validate_secure_mime

        if not validate_secure_mime(proof, PAYMENT_PROOF_MIMES):
            flash('Only PDF, JPG, and PNG proof files are allowed.', 'error')
            return redirect(url_for('payments.project_overview', project_id=project.id))

        proof_path = save_file_securely(proof, filename_prefix=f'payment_{project.id}_{current_user.id}')
        now = utc_now()
        with _transaction():
            payment_log = PaymentLog(
                project_id=project.id,
                amount=amount,
                paid_by=paid_by,
                paid_to=paid_to,
                description=description,
                category=category,
                stage=stage,
                payment_method=payment_method,
                proof_path=proof_path,
                status='pending' if current_user.role == 'client' else 'confirmed',
                comment=note or None,
                created_by=current_user.id,
                approved_by=current_user.id if current_user.role == 'architect' else None,
                approved_at=now if current_user.role == 'architect' else None,
                created_at=now,
            )
            db.session.add(payment_log)
            db.session.add(AuditLog(
                project_id=project.id,
                actor_id=current_user.id,
                action='PAYMENT_LOGGED' if current_user.role == 'client' else 'EXPENSE_LOGGED',
                description=f'{current_user.name} logged ₹{amount:,.0f} for {description}.',
                is_client_visible=True,
            ))
    except Exception as exc:
        db.session.rollback()
        if proof_path:
            delete_file_securely(proof_path)
        current_app.logger.error(f'Payment add error: {exc}')
        flash('Unable to save the payment right now. Please try again.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    # Bridge proof file to Document Vault (stream already consumed; use path-based helper)
    try:
        from app.services.document_service import create_vault_record_from_path
        from app.models import Document, DocumentVersion
        payment_name = (
            f"Payment Proof — {payment_log.category} — ₹{payment_log.amount:,.0f}"
        )
        vault_doc, _ = create_vault_record_from_path(
            file_path=proof_path,
            original_filename=proof.filename,
            project_id=project.id,
            uploaded_by=current_user.id,
            uploaded_by_role=current_user.role,
            source_module='payments',
            display_name=payment_name,
            visible_to_client=True,
        )
        payment_log.document_id = vault_doc.id
        db.session.commit()
    except Exception:
        pass  # vault bridging is best-effort; do not fail the payment

    if current_user.role == 'client':
        _notify_counterparty(project, 'Payment update', f'{current_user.name} logged a client payment of ₹{amount:,.0f} for {project.name}.')
        flash('Payment logged and sent for architect confirmation.', 'success')
    else:
        _notify_counterparty(project, 'Project expense update', f'{current_user.name} logged a project expense of ₹{amount:,.0f} for {project.name}.')
        flash('Expense logged and added to the shared ledger.', 'success')

    return redirect(url_for('payments.project_overview', project_id=project.id))


@bp.route('/payments/<int:payment_log_id>/approve', methods=['POST'])
@login_required
def approve(payment_log_id):
    require_architect()
    payment_log = PaymentLog.query.get_or_404(payment_log_id)
    project = _get_project_or_403(payment_log.project_id)

    if payment_log.paid_by != 'client':
        abort(400)
    if payment_log.status != 'pending':
        flash('Only pending client payments can be confirmed.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    with _transaction():
        payment_log.status = 'confirmed'
        payment_log.approved_by = current_user.id
        payment_log.approved_at = utc_now()
        payment_log.comment = _append_comment(payment_log.comment, current_user.name, request.form.get('comment'))
        db.session.add(AuditLog(
            project_id=project.id,
            actor_id=current_user.id,
            action='PAYMENT_CONFIRMED',
            description=f'Architect confirmed client payment #{payment_log.id}.',
            is_client_visible=True,
        ))

    if project.client:
        notify_user_comms(project.client, 'Payment confirmed', f'Your payment of ₹{payment_log.amount:,.0f} for {project.name} has been confirmed.')

    flash('Client payment confirmed.', 'success')
    return redirect(url_for('payments.project_overview', project_id=project.id))


@bp.route('/payments/<int:payment_log_id>/reject', methods=['POST'])
@login_required
def reject(payment_log_id):
    require_architect()
    payment_log = PaymentLog.query.get_or_404(payment_log_id)
    project = _get_project_or_403(payment_log.project_id)

    if payment_log.paid_by != 'client':
        abort(400)
    if payment_log.status != 'pending':
        flash('Only pending client payments can be rejected.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    with _transaction():
        payment_log.status = 'rejected'
        payment_log.approved_by = current_user.id
        payment_log.approved_at = utc_now()
        payment_log.comment = _append_comment(payment_log.comment, current_user.name, request.form.get('comment'))
        db.session.add(AuditLog(
            project_id=project.id,
            actor_id=current_user.id,
            action='PAYMENT_REJECTED',
            description=f'Architect rejected client payment #{payment_log.id}.',
            is_client_visible=True,
        ))

    if project.client:
        notify_user_comms(project.client, 'Payment rejected', f'Your payment entry for {project.name} was rejected. Please review the ledger note.')

    flash('Client payment rejected.', 'success')
    return redirect(url_for('payments.project_overview', project_id=project.id))


@bp.route('/payments/<int:payment_log_id>/comment', methods=['POST'])
@login_required
def comment(payment_log_id):
    if current_user.role != 'client':
        abort(403)

    payment_log = PaymentLog.query.get_or_404(payment_log_id)
    project = _get_project_or_403(payment_log.project_id)
    if payment_log.paid_by != 'architect':
        abort(400)

    text = (request.form.get('comment') or '').strip()
    if not text:
        flash('Please write a comment before saving.', 'error')
        return redirect(url_for('payments.project_overview', project_id=project.id))

    with _transaction():
        payment_log.comment = _append_comment(payment_log.comment, current_user.name, text)
        db.session.add(AuditLog(
            project_id=project.id,
            actor_id=current_user.id,
            action='PAYMENT_COMMENT',
            description=f'Client commented on expense #{payment_log.id}.',
            is_client_visible=True,
        ))

    if project.architect:
        notify_user_comms(project.architect, 'Expense comment added', f'{current_user.name} commented on expense #{payment_log.id} in {project.name}.')

    flash('Comment saved on the payment row.', 'success')
    return redirect(url_for('payments.project_overview', project_id=project.id))


@bp.route('/payments/<int:payment_log_id>/download-proof')
@login_required
def download_proof(payment_log_id):
    payment_log = PaymentLog.query.get_or_404(payment_log_id)
    _get_project_or_403(payment_log.project_id)

    if not payment_log.proof_path:
        abort(404)

    from app.storage import send_file_securely
    return send_file_securely(payment_log.proof_path)

