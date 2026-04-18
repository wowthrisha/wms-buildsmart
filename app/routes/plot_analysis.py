import json

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app import db
from app.auth import require_architect
from app.compliance_service import compliance_summary, ensure_project_compliance_items
from app.models import AuditLog, ComplianceItem, DocumentVersion, PlotAnalysis, PlotAnalysisVersion, PlotComplianceResult, PlotConflict, PlotDocument as PlotDoc, PlotExtractedData, Project
from app.time_utils import utc_now
from app.tn_engine import check_compliance, get_engine

bp = Blueprint('plot_analysis', __name__)

ENGINE = get_engine()

BUILDING_OPTIONS = sorted(ENGINE.VALID_BUILDING_TYPES)
LOCATION_OPTIONS = sorted(ENGINE.VALID_LOCATION_TYPES)
CMDA_ZONE_OPTIONS = sorted(ENGINE.VALID_CMDA_ZONES)

PLOT_INPUT_FIELDS = [
    ('plot_area', 'Plot Area', 'sq.m'),
    ('road_width', 'Road Width', 'm'),
    ('frontage', 'Frontage', 'm'),
    ('height_m', 'Height', 'm'),
    ('fsi_proposed', 'FSI Proposed', ''),
    ('coverage_pct', 'Coverage', '%'),
    ('front_setback_m', 'Front Setback', 'm'),
    ('side_setback_m', 'Side Setback', 'm'),
    ('rear_setback_m', 'Rear Setback', 'm'),
]
PLOT_FIELD_NAMES = tuple(field_name for field_name, _, _ in PLOT_INPUT_FIELDS)
PLOT_FIELD_LABELS = {field_name: label for field_name, label, _ in PLOT_INPUT_FIELDS}
PLOT_FIELD_UNITS = {field_name: unit for field_name, _, unit in PLOT_INPUT_FIELDS}


def _is_ajax() -> bool:
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.accept_mimetypes.best == 'application/json'
        or request.is_json
    )


def _load_project(project_id: int) -> Project:
    project = Project.query.get_or_404(project_id)
    if project.architect_id != current_user.id:
        abort(403)
    return project


def _latest_analysis(project_id: int):
    return (
        PlotAnalysis.query
        .filter_by(project_id=project_id)
        .order_by(PlotAnalysis.created_at.desc())
        .first()
    )


def _analysis_or_404(project_id: int, analysis_id: int):
    project = _load_project(project_id)
    analysis = PlotAnalysis.query.filter_by(id=analysis_id, project_id=project_id).first_or_404()
    return project, analysis


def _load_input_payload(analysis: PlotAnalysis | None) -> dict:
    if not analysis or not analysis.input_payload:
        return {}
    try:
        return json.loads(analysis.input_payload)
    except Exception:
        return {}


def _load_result_payload(analysis: PlotAnalysis | None) -> dict | None:
    if not analysis or not analysis.result_payload:
        return None
    try:
        return json.loads(analysis.result_payload)
    except Exception:
        return None


def _safe_float(raw):
    if raw in (None, ''):
        return None
    return float(raw)


def _collect_input(payload) -> dict:
    fields = {}
    numeric_keys = {
        'plot_area',
        'road_width',
        'frontage',
        'height_m',
        'fsi_proposed',
        'coverage_pct',
        'front_setback_m',
        'side_setback_m',
        'rear_setback_m',
    }

    for key in numeric_keys:
        raw = payload.get(key)
        if raw in (None, ''):
            continue
        fields[key] = _safe_float(raw)

    for key in ('building_type', 'location_type', 'cmda_zone'):
        value = (payload.get(key) or '').strip()
        if value:
            fields[key] = value

    fields['setbacks'] = {
        'front_setback_m': fields.get('front_setback_m'),
        'side_setback_m': fields.get('side_setback_m'),
        'rear_setback_m': fields.get('rear_setback_m'),
    }
    return fields


def _analysis_badge(result_payload: dict | None) -> dict:
    if not result_payload:
        return {'label': 'Not Run', 'tone': 'muted'}
    if not result_payload.get('valid'):
        return {'label': 'Input Errors', 'tone': 'red'}
    if result_payload.get('compliant'):
        return {'label': 'Compliant', 'tone': 'green'}
    return {'label': 'Needs Attention', 'tone': 'amber'}


def _result_summary(result_payload: dict | None) -> dict:
    if not result_payload:
        return {'violations': 0, 'warnings': 0}
    return {
        'violations': len(result_payload.get('violations') or []),
        'warnings': len(result_payload.get('warnings') or []),
    }


def _applied_rule_rows(result_payload: dict | None) -> list[dict]:
    if not result_payload:
        return []
    rules = result_payload.get('applied_rules') or {}
    rows = []
    for key in (
        'source',
        'rule_reference',
        'min_road_width_m',
        'min_plot_area_sqm',
        'min_frontage_m',
        'max_height_m',
        'fsi',
        'max_coverage_pct',
        'high_rise',
    ):
        if key in rules:
            rows.append({
                'label': key.replace('_', ' ').title(),
                'value': rules[key],
            })
    return rows


def _rule_meta_for_field(field_name: str, applied_rules: dict | None) -> tuple[float | None, str | None]:
    rules = applied_rules or {}
    setback_rules = rules.get('setback') or {}
    mapping = {
        'plot_area': ('min_plot_area_sqm', 'min'),
        'road_width': ('min_road_width_m', 'min'),
        'frontage': ('min_frontage_m', 'min'),
        'height_m': ('max_height_m', 'max'),
        'coverage_pct': ('max_coverage_pct', 'max'),
        'front_setback_m': ('front_m', 'min'),
        'side_setback_m': ('side_m', 'min'),
        'rear_setback_m': ('rear_m', 'min'),
    }
    if field_name == 'fsi_proposed':
        limit = rules.get('fsi')
        if isinstance(limit, dict):
            limit = next((value for value in limit.values() if value is not None), None)
        return limit, 'max'
    if field_name in ('front_setback_m', 'side_setback_m', 'rear_setback_m'):
        limit_key, mode = mapping[field_name]
        return setback_rules.get(limit_key), mode
    limit_key, mode = mapping.get(field_name, (None, None))
    return rules.get(limit_key) if limit_key else None, mode


def _format_rule_limit(field_name: str, limit: float | None, mode: str | None) -> str:
    if limit is None or mode is None:
        return 'Manual review required'
    prefix = '>=' if mode == 'min' else '<='
    unit = PLOT_FIELD_UNITS.get(field_name) or ''
    return f'{prefix} {limit} {unit}'.strip()


def _format_actual_value(field_name: str, value) -> str:
    if value in (None, ''):
        return 'Not provided'
    unit = PLOT_FIELD_UNITS.get(field_name) or ''
    return f'{value} {unit}'.strip()


def _build_rule_rows(fields: dict, result: dict) -> list[dict]:
    applied_rules = result.get('applied_rules') or {}
    violations = {row.get('field'): row for row in (result.get('violations') or [])}
    rows = []
    for field_name in PLOT_FIELD_NAMES:
        value = fields.get(field_name)
        limit, mode = _rule_meta_for_field(field_name, applied_rules)
        expected_value = _format_rule_limit(field_name, limit, mode)
        actual_value = _format_actual_value(field_name, value)
        if value in (None, ''):
            status = 'not_available'
            score = None
            suggestion = 'Provide this field to evaluate the rule.'
        elif limit is None:
            status = 'warning'
            score = 50.0
            suggestion = 'Manual verification required for this rule.'
        elif field_name in violations:
            status = 'fail'
            score = 0.0
            suggestion = violations[field_name].get('msg') or 'Rule does not meet the required limit.'
        else:
            status = 'pass'
            score = 100.0
            suggestion = 'Rule satisfied.'
        rows.append({
            'rule_name': field_name,
            'rule_label': PLOT_FIELD_LABELS.get(field_name, field_name.replace('_', ' ').title()),
            'expected_value': expected_value,
            'actual_value': actual_value,
            'score': score,
            'status': status,
            'suggestion': suggestion,
        })
    return rows


def _score_rule_rows(rows: list[dict]) -> tuple[float, float, str]:
    scored_rules = [row for row in rows if row.get('score') is not None]
    total_rules = len(rows)
    if total_rules == 0:
        return 0.0, 0.0, 'NO_DATA'

    data_completeness = len(scored_rules) / total_rules
    if not scored_rules:
        overall_score = 0.0
        trust_level = 'INSUFFICIENT'
    else:
        raw_avg = sum(row['score'] for row in scored_rules) / len(scored_rules)
        overall_score = round(raw_avg * data_completeness, 1)
        if data_completeness < 0.30:
            trust_level = 'INSUFFICIENT'
        elif data_completeness < 0.60:
            trust_level = 'PARTIAL'
        elif data_completeness < 0.90:
            trust_level = 'PROVISIONAL'
        else:
            trust_level = 'FULL'

    return overall_score, round(data_completeness * 100, 1), trust_level


def _sync_rule_rows(analysis: PlotAnalysis, rows: list[dict]) -> None:
    analysis.results.delete()
    for row in rows:
        db.session.add(PlotComplianceResult(
            analysis_id=analysis.id,
            rule_name=row['rule_name'],
            rule_label=row['rule_label'],
            expected_value=row['expected_value'],
            actual_value=row['actual_value'],
            score=row['score'],
            status=row['status'],
            suggestion=row['suggestion'],
        ))


def _normalize_analysis_result(fields: dict, result: dict) -> dict:
    payload = dict(result or {})
    rows = _build_rule_rows(fields, payload)
    if not payload.get('valid'):
        overall_score, data_completeness, trust_level = 0.0, 0.0, 'INSUFFICIENT'
    else:
        overall_score, data_completeness, trust_level = _score_rule_rows(rows)
        if trust_level == 'NO_DATA':
            trust_level = 'INSUFFICIENT'
    payload['overall_score'] = overall_score
    payload['data_completeness'] = data_completeness
    payload['trust_level'] = trust_level
    payload['rule_results'] = rows
    return payload


def _sync_compliance_from_analysis(analysis: PlotAnalysis, project_id: int) -> None:
    try:
        from app.models import PlotDocument as _PlotDoc
        SYNC_MAP = {'FMB': 'fmb', 'Patta': 'patta', 'EC': 'ec'}
        plot_docs = _PlotDoc.query.filter_by(analysis_id=analysis.id, status='uploaded').all()
        for plot_doc in plot_docs:
            comp_type = SYNC_MAP.get(plot_doc.doc_type)
            if not comp_type:
                continue
            comp_item = ComplianceItem.query.filter_by(
                project_id=project_id, doc_type=comp_type
            ).first()
            if comp_item and comp_item.status in ('missing', 'optional'):
                comp_item.status = 'uploaded'
                if plot_doc.document_id:
                    comp_item.document_id = plot_doc.document_id
        db.session.commit()
    except Exception as e:
        from flask import current_app
        current_app.logger.warning(f'Compliance sync failed: {e}')


def _save_analysis(project_id: int, fields: dict, result: dict, *, analysis: PlotAnalysis | None = None) -> PlotAnalysis:
    if analysis is None:
        analysis = _latest_analysis(project_id)
    if analysis is None:
        analysis = PlotAnalysis(
            project_id=project_id,
            uploaded_by=current_user.id,
            status='pending',
            original_filename='Manual Input',
        )
        db.session.add(analysis)
        db.session.flush()

    normalized_result = _normalize_analysis_result(fields, result)
    analysis.input_payload = json.dumps(fields)
    analysis.result_payload = json.dumps(normalized_result)
    analysis.processed_json = analysis.result_payload
    analysis.overall_score = normalized_result.get('overall_score')
    analysis.data_completeness = normalized_result.get('data_completeness')
    analysis.data_completeness_score = normalized_result.get('data_completeness')
    trust_level = normalized_result.get('trust_level')
    analysis.trust_level = trust_level
    if not normalized_result.get('valid'):
        analysis.status = 'pending'
    elif (trust_level or '').upper() == 'INSUFFICIENT':
        analysis.status = 'insufficient_data'
    else:
        analysis.status = 'analyzed'
        _sync_compliance_from_analysis(analysis, project_id)
    analysis.updated_at = utc_now()
    _sync_rule_rows(analysis, normalized_result.get('rule_results') or [])
    return analysis


@bp.route('/plot-analysis')
@login_required
def index():
    require_architect()
    projects = (
        Project.query
        .filter_by(architect_id=current_user.id)
        .options(joinedload(Project.client))
        .all()
    )
    return render_template('architect/plot_analysis_overview.html', projects=projects)


@bp.route('/projects/<int:project_id>/plot-analysis')
@login_required
def project_overview(project_id):
    require_architect()
    project = (
        Project.query
        .options(joinedload(Project.client))
        .get_or_404(project_id)
    )
    if project.architect_id != current_user.id:
        abort(403)

    analysis = _latest_analysis(project.id)
    input_payload = _load_input_payload(analysis)
    result_payload = _load_result_payload(analysis)

    return render_template(
        'plot_analysis.html',
        project=project,
        active_project=project,
        active_tab='plot',
        analysis=analysis,
        input_payload=input_payload,
        result_payload=result_payload,
        applied_rule_rows=_applied_rule_rows(result_payload),
        analysis_badge=_analysis_badge(result_payload),
        result_summary=_result_summary(result_payload),
        documents_summary=compliance_summary(project),
        building_options=BUILDING_OPTIONS,
        location_options=LOCATION_OPTIONS,
        cmda_zone_options=CMDA_ZONE_OPTIONS,
        field_meta=PLOT_INPUT_FIELDS,
    )


_UPLOAD_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_ALLOWED_UPLOAD_MIMES = {'image/png', 'image/jpeg', 'image/jpg'}


@bp.route('/projects/<int:project_id>/plot-analysis/upload', methods=['POST'])
@login_required
def upload(project_id):
    """
    Accept an image drawing (PNG/JPG), create a PlotAnalysis + PlotDocument,
    run OCR if available. Returns JSON when AJAX.

    Query params / form fields:
      drawing  — image file
      doc_type — optional: 'FMB'|'Patta'|'EC' (defaults to 'FMB')
    """
    require_architect()
    project = _load_project(project_id)

    file = request.files.get('drawing') or request.files.get('file')
    if not file or not file.filename:
        if _is_ajax():
            return jsonify({'ok': False, 'error': 'No file provided.'}), 400
        flash('No file provided.', 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))

    # Validate image MIME (reject PDF etc.) using Pillow (imghdr removed in Python 3.13)
    raw = file.read(512)
    file.seek(0)
    content_type = (file.content_type or '').lower()
    _is_valid_image = False
    try:
        from PIL import Image
        import io as _io
        file.seek(0)
        full = file.read()
        file.seek(0)
        Image.open(_io.BytesIO(full)).verify()
        _is_valid_image = True
    except Exception:
        _is_valid_image = False
    if not _is_valid_image:
        if _is_ajax():
            return jsonify({'ok': False, 'error': 'Invalid image file: only PNG and JPG are supported.'}), 400
        flash('Only PNG and JPG drawings are accepted.', 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))

    # Validate size
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > _UPLOAD_MAX_BYTES:
        if _is_ajax():
            return jsonify({'ok': False, 'error': 'File too large. Maximum allowed size is 5 MB.'}), 400
        flash('File too large (max 5 MB).', 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))

    doc_type = (request.form.get('doc_type') or 'FMB').strip()
    original_filename = file.filename

    from app.storage import save_file_securely
    file_path = save_file_securely(file)

    # Create or reuse PlotAnalysis
    analysis = _latest_analysis(project.id)
    if analysis is None:
        analysis = PlotAnalysis(
            project_id=project.id,
            uploaded_by=current_user.id,
            file_path=file_path,
            original_filename=original_filename,
            status='pending',
        )
        db.session.add(analysis)
        db.session.flush()

    # Create or replace PlotDocument for this doc_type
    plot_doc = PlotDoc.query.filter_by(
        analysis_id=analysis.id, doc_type=doc_type
    ).first()
    if plot_doc:
        plot_doc.file_path = file_path
        plot_doc.original_filename = original_filename
        plot_doc.status = 'uploaded'
        plot_doc.uploaded_by = current_user.id
    else:
        plot_doc = PlotDoc(
            analysis_id=analysis.id,
            doc_type=doc_type,
            file_path=file_path,
            original_filename=original_filename,
            status='uploaded',
            uploaded_by=current_user.id,
            created_at=utc_now(),
        )
        db.session.add(plot_doc)

    # Try OCR pipeline (touches only extraction, not rules)
    extracted = {}
    n_extracted = 0
    try:
        from app.ocr import extract_dimensions
        result = extract_dimensions(file_path)
        if isinstance(result, dict):
            extracted = {k: v for k, v in result.items() if k != '_raw_text' and v is not None}
            n_extracted = len(extracted)
            if result.get('_raw_text'):
                plot_doc.raw_text = result['_raw_text']
        analysis.status = 'extracted'
    except Exception:
        analysis.status = 'pending'

    db.session.add(AuditLog(
        project_id=project.id,
        actor_id=current_user.id,
        action='UPLOAD',
        description=f'Uploaded {doc_type} drawing for plot analysis',
    ))
    db.session.flush()  # flush before vault bridge so plot_doc.id is set

    # Bridge to Document Vault — file stream is already consumed, use path-based helper
    _PA_COMP_MAP = {'FMB': 'fmb', 'Patta': 'patta', 'EC': 'ec'}
    _PA_LABELS   = {'FMB': 'FMB Sketch', 'Patta': 'Patta / Chitta', 'EC': 'Encumbrance Certificate'}
    compliance_key = _PA_COMP_MAP.get(doc_type)
    try:
        from app.services.document_service import create_vault_record_from_path
        vault_doc, _ = create_vault_record_from_path(
            file_path=file_path,
            original_filename=original_filename,
            project_id=project.id,
            uploaded_by=current_user.id,
            uploaded_by_role='architect',
            source_module='plot_analysis',
            display_name=f'{_PA_LABELS.get(doc_type, doc_type)} — Plot Analysis',
            compliance_doc_type=compliance_key,
            visible_to_client=False,
            existing_document_id=plot_doc.document_id if plot_doc.document_id else None,
        )
        plot_doc.document_id = vault_doc.id
        # Sync ComplianceItem if applicable
        if compliance_key:
            comp_item = ComplianceItem.query.filter_by(
                project_id=project.id, doc_type=compliance_key
            ).first()
            if comp_item and not comp_item.document_id:
                comp_item.document_id = vault_doc.id
                comp_item.status = 'uploaded'
    except Exception:
        pass  # vault bridging is best-effort; do not fail the upload

    db.session.commit()

    if _is_ajax():
        return jsonify({
            'ok': True,
            'analysis_id': analysis.id,
            'doc_id': plot_doc.id,
            'n_extracted': n_extracted,
            'extracted': extracted,
        })

    flash('Drawing uploaded.', 'success')
    return redirect(url_for('plot_analysis.project_overview', project_id=project.id))


@bp.route('/projects/<int:project_id>/plot-analysis/compute', methods=['POST'])
@login_required
def compute(project_id):
    require_architect()
    project = _load_project(project_id)

    try:
        fields = _collect_input(request.get_json(silent=True) or request.form)
        result = check_compliance(fields)
        analysis = _save_analysis(project.id, fields, result)
        db.session.add(AuditLog(
            project_id=project.id,
            actor_id=current_user.id,
            action='UPDATE',
            description='Ran plot analysis using tn_rules.py',
            is_client_visible=True,
        ))
        db.session.commit()

        if _is_ajax():
            return jsonify({
                'ok': True,
                'analysis_id': analysis.id,
                'result': _load_result_payload(analysis),
            })

        flash('Plot analysis updated.', 'success')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))
    except ValueError as exc:
        db.session.rollback()
        if _is_ajax():
            return jsonify({'ok': False, 'error': str(exc)}), 400
        flash(f'Invalid number: {exc}', 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/json')
@login_required
def analysis_json(project_id, analysis_id):
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)

    # Fields from PlotExtractedData
    fields = [
        {
            'field_name': f.field_name,
            'value': f.value,
            'normalized_value': f.normalized_value,
            'unit': f.unit,
            'confidence': f.confidence,
            'source': f.source,
            'is_manual_override': bool(f.is_manual_override),
        }
        for f in analysis.fields.order_by(PlotExtractedData.field_name)
    ]

    # Documents linked to this analysis
    documents = [
        {'id': d.id, 'doc_type': d.doc_type, 'status': d.status,
         'original_filename': d.original_filename}
        for d in analysis.documents.all()
    ]

    # Compliance results
    compliance_results = [
        {'rule_name': r.rule_name, 'status': r.status, 'score': r.score,
         'expected_value': r.expected_value, 'actual_value': r.actual_value}
        for r in analysis.results.all()
    ]

    return jsonify({
        'analysis_id': analysis.id,
        'project_id': project.id,
        'project_name': project.name,
        'status': analysis.status,
        'overall_score': analysis.overall_score,
        'data_completeness': analysis.data_completeness,
        'data_completeness_score': analysis.data_completeness_score,
        'trust_level': analysis.trust_level,
        'road_direction': analysis.road_direction,
        'authority': analysis.authority,
        'input': _load_input_payload(analysis),
        'result': _load_result_payload(analysis),
        'fields': fields,
        'documents': documents,
        'compliance_results': compliance_results,
    })


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/edit', methods=['POST'])
@login_required
def edit(project_id, analysis_id):
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)
    try:
        form_data = request.get_json(silent=True) or request.form
        fields = _collect_input(form_data)
        result = check_compliance(fields)
        _save_analysis(project.id, fields, result, analysis=analysis)

        # Save manual overrides using canonical PlotExtractedData field names.
        for field_name in PLOT_FIELD_NAMES:
            raw = form_data.get(field_name)
            if raw in (None, ''):
                continue
            try:
                val = float(raw)
            except (ValueError, TypeError):
                continue
            existing = PlotExtractedData.query.filter_by(
                analysis_id=analysis.id, field_name=field_name, source='manual'
            ).first()
            if existing:
                existing.value = str(raw)
                existing.normalized_value = str(val)
                existing.unit = PLOT_FIELD_UNITS.get(field_name) or None
                existing.confidence = -1.0
                existing.is_manual_override = True
            else:
                db.session.add(PlotExtractedData(
                    analysis_id=analysis.id,
                    field_name=field_name,
                    value=str(raw),
                    normalized_value=str(val),
                    unit=PLOT_FIELD_UNITS.get(field_name) or None,
                    confidence=-1.0,
                    is_manual_override=True,
                    source='manual',
                ))

        db.session.commit()
        flash('Plot analysis inputs saved.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(f'Invalid number: {exc}', 'error')
    return redirect(url_for('plot_analysis.project_overview', project_id=project.id))


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/run-analysis', methods=['POST'])
@login_required
def run_analysis(project_id, analysis_id):
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)

    field_map = _load_input_payload(analysis)
    field_map.update(_extracted_field_map(analysis.id))

    result = check_compliance(field_map)
    _save_analysis(project.id, field_map, result, analysis=analysis)
    db.session.commit()
    result_payload = _load_result_payload(analysis) or {}

    if _is_ajax():
        return jsonify({
            'ok': True,
            'overall_score': analysis.overall_score,
            'completeness': analysis.data_completeness,
            'trust_level': analysis.trust_level,
            'result': result_payload,
        })
    flash('Plot analysis re-run complete.', 'success')
    return redirect(url_for('plot_analysis.result', project_id=project.id, analysis_id=analysis.id))


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/result')
@login_required
def result(project_id, analysis_id):
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)
    result_payload = _load_result_payload(analysis)
    return render_template(
        'architect/plot_analysis_result.html',
        project=project,
        active_project=project,
        active_tab='plot',
        analysis=analysis,
        rule_rows=analysis.results.order_by(PlotComplianceResult.rule_label.asc()).all(),
        input_payload=_load_input_payload(analysis),
        result_payload=result_payload,
        applied_rule_rows=_applied_rule_rows(result_payload),
        analysis_badge=_analysis_badge(result_payload),
        result_summary=_result_summary(result_payload),
    )


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/confirm', methods=['POST'])
@login_required
def confirm(project_id, analysis_id):
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)
    result_payload = _load_result_payload(analysis)
    if (analysis.trust_level or '').upper() == 'INSUFFICIENT':
        msg = 'Fill more fields before confirming this analysis.'
        if _is_ajax():
            return jsonify({'ok': False, 'error': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))
    # Allow confirm if result_payload is valid OR if analysis was computed via compute route
    payload_valid = result_payload and result_payload.get('valid')
    if not payload_valid and analysis.status != 'analyzed':
        msg = 'Run a valid plot analysis before confirming it.'
        if _is_ajax():
            return jsonify({'ok': False, 'error': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))

    analysis.status = 'confirmed'
    analysis.updated_at = utc_now()
    db.session.add(AuditLog(
        project_id=project.id,
        actor_id=current_user.id,
        action='UPDATE',
        description='Confirmed plot analysis',
        is_client_visible=True,
    ))
    db.session.commit()
    if _is_ajax():
        return jsonify({'ok': True, 'status': analysis.status})
    flash('Plot analysis confirmed.', 'success')
    return redirect(url_for('plot_analysis.project_overview', project_id=project.id))


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/report')
@login_required
def report(project_id, analysis_id):
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)
    result_payload = _load_result_payload(analysis)
    if (analysis.trust_level or '').upper() == 'INSUFFICIENT':
        flash('Fill more fields before generating the report.', 'error')
        return redirect(url_for('plot_analysis.result', project_id=project.id, analysis_id=analysis.id))
    # Redirect only if there's no payload AND analysis hasn't been computed
    if not result_payload and analysis.status not in ('analyzed', 'confirmed'):
        flash('Run plot analysis before opening the report.', 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))
    return render_template(
        'architect/plot_analysis_report.html',
        project=project,
        analysis=analysis,
        input_payload=_load_input_payload(analysis),
        result_payload=result_payload,
        applied_rule_rows=_applied_rule_rows(result_payload),
        analysis_badge=_analysis_badge(result_payload),
    )


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/save-version', methods=['POST'])
@login_required
def save_version(project_id, analysis_id):
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)
    version_name = request.form.get('version_name', '').strip()
    if not version_name:
        flash('Please enter a version name.', 'error')
        return redirect(url_for('plot_analysis.result', project_id=project.id, analysis_id=analysis.id))

    snapshot = {
        'input': _load_input_payload(analysis),
        'result': _load_result_payload(analysis),
    }
    db.session.add(PlotAnalysisVersion(
        analysis_id=analysis.id,
        project_id=project.id,
        version_name=version_name,
        snapshot_json=json.dumps(snapshot),
        created_by=current_user.id,
    ))
    db.session.commit()
    if _is_ajax():
        return jsonify({'ok': True, 'version_name': version_name})
    flash(f'Version "{version_name}" saved.', 'success')
    return redirect(url_for('plot_analysis.result', project_id=project.id, analysis_id=analysis.id))


# Map plot_analysis doc_type strings to compliance_doc_type keys
_PA_COMPLIANCE_MAP = {
    'FMB':   'fmb',
    'Patta': 'patta',
    'EC':    'ec',
}
_PA_DISPLAY_NAMES = {
    'FMB':   'FMB Sketch',
    'Patta': 'Patta / Chitta',
    'EC':    'Encumbrance Certificate',
}


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/upload-document', methods=['POST'])
@login_required
def upload_document(project_id, analysis_id):
    """
    Upload FMB / Patta / EC into the central Document vault AND create/update
    a PlotDocument record linked to this analysis.
    The PlotDocument.document_id bridge FK ensures the file appears in the
    Document Vault and links back to the compliance checklist.
    OCR pipeline is NOT called here — that remains untouched.
    """
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)

    doc_type = (request.form.get('doc_type') or '').strip()  # 'FMB'|'Patta'|'EC'
    file = request.files.get('file')

    if not doc_type or doc_type not in _PA_DISPLAY_NAMES:
        if _is_ajax():
            return jsonify({'ok': False, 'error': f'doc_type must be one of: {list(_PA_DISPLAY_NAMES)}'})
        from flask import flash
        flash('Invalid document type.', 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))

    if not file or not file.filename:
        if _is_ajax():
            return jsonify({'ok': False, 'error': 'No file provided.'})
        from flask import flash
        flash('No file provided.', 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))

    compliance_key = _PA_COMPLIANCE_MAP.get(doc_type)
    display_name = _PA_DISPLAY_NAMES[doc_type]

    # Find existing PlotDocument for this analysis + doc_type
    existing_plot_doc = PlotDoc.query.filter_by(
        analysis_id=analysis.id, doc_type=doc_type
    ).first()
    existing_doc_id = existing_plot_doc.document_id if existing_plot_doc else None

    from app.services.document_service import create_or_replace_document
    try:
        doc, _ = create_or_replace_document(
            file=file,
            project_id=project.id,
            uploaded_by=current_user.id,
            uploaded_by_role='architect',
            source_module='plot_analysis',
            display_name=f'{display_name} — Plot Analysis',
            compliance_doc_type=compliance_key,
            plot_analysis_id=analysis.id,
            visible_to_client=False,
            existing_document_id=existing_doc_id,
        )
    except ValueError as e:
        if _is_ajax():
            return jsonify({'ok': False, 'error': str(e)})
        from flask import flash
        flash(str(e), 'error')
        return redirect(url_for('plot_analysis.project_overview', project_id=project.id))

    # Get the saved filename from latest version
    latest_ver = DocumentVersion.query.filter_by(document_id=doc.id).order_by(DocumentVersion.version_num.desc()).first()
    file_path = latest_ver.filename if latest_ver else doc.filename

    # Create or update PlotDocument
    if existing_plot_doc:
        existing_plot_doc.document_id = doc.id
        existing_plot_doc.file_path = file_path
        existing_plot_doc.status = 'uploaded'
        plot_doc = existing_plot_doc
    else:
        plot_doc = PlotDoc(
            analysis_id=analysis.id,
            doc_type=doc_type,
            document_id=doc.id,
            file_path=file_path,
            original_filename=file_path,
            status='uploaded',
            uploaded_by=current_user.id,
            created_at=utc_now(),
        )
        db.session.add(plot_doc)

    # Sync ComplianceItem if one exists and not already linked
    if compliance_key:
        ensure_project_compliance_items(project.id)
        comp_item = ComplianceItem.query.filter_by(
            project_id=project.id,
            doc_type=compliance_key,
        ).first()
        if comp_item:
            comp_item.document_id = doc.id
            comp_item.status = 'uploaded'
            comp_item.updated_at = utc_now()

    db.session.add(AuditLog(
        project_id=project.id,
        actor_id=current_user.id,
        action='UPLOAD',
        description=f'Uploaded {display_name} for plot analysis #{analysis.id}',
        is_client_visible=False,
    ))
    db.session.commit()

    if _is_ajax():
        return jsonify({
            'ok': True,
            'document_id': doc.id,
            'plot_document_id': plot_doc.id,
            'doc_type': doc_type,
        })
    from flask import flash
    flash(f'{display_name} uploaded.', 'success')
    return redirect(url_for('plot_analysis.project_overview', project_id=project.id))


# ── Source priority for conflict resolution ────────────────────────────────
_SOURCE_PRIORITY = {'manual': 0, 'FMB': 1, 'Patta': 2, 'EC': 3}


def _best_field_value(analysis_id: int, field_name: str):
    """
    Return the best PlotExtractedData row for this field, using source priority:
    manual (override) > FMB > Patta > EC > None-source (legacy).
    """
    rows = PlotExtractedData.query.filter_by(
        analysis_id=analysis_id, field_name=field_name
    ).all()
    if not rows:
        return None
    # manual override always wins
    manual = next((r for r in rows if r.is_manual_override), None)
    if manual:
        return manual
    def _prio(r):
        return _SOURCE_PRIORITY.get(r.source, 99)
    return min(rows, key=_prio)


def _extracted_field_map(analysis_id: int) -> dict:
    """Build a canonical field_map dict from PlotExtractedData rows."""
    field_map = {}
    for field_name in PLOT_FIELD_NAMES:
        best = _best_field_value(analysis_id, field_name)
        if best and best.float_value is not None:
            field_map[field_name] = best.float_value
    return field_map


@bp.route('/projects/<int:project_id>/plot-analysis/manual', methods=['POST'])
@login_required
def manual(project_id):
    """
    Save manual context inputs (road_direction, authority) and numeric field
    overrides as PlotExtractedData rows with source='manual'.
    """
    require_architect()
    project = _load_project(project_id)

    data = request.get_json(silent=True) or request.form
    errors = []

    road_direction = (data.get('road_direction') or '').strip().upper()
    authority = (data.get('authority') or '').strip()

    _VALID_DIRECTIONS = {'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW'}
    if road_direction and road_direction not in _VALID_DIRECTIONS:
        errors.append(f'Invalid road_direction: must be one of {sorted(_VALID_DIRECTIONS)}')

    if errors:
        return jsonify({'ok': False, 'errors': errors}), 400

    # Get or create PlotAnalysis
    analysis = _latest_analysis(project.id)
    if analysis is None:
        analysis = PlotAnalysis(
            project_id=project.id, uploaded_by=current_user.id,
            original_filename='Manual Input', status='pending',
        )
        db.session.add(analysis)
        db.session.flush()

    if road_direction:
        analysis.road_direction = road_direction
    if authority:
        analysis.authority = authority

    # Save numeric field overrides
    saved = {}
    for field_name in PLOT_FIELD_NAMES:
        raw = data.get(field_name)
        if raw in (None, ''):
            continue
        try:
            val = float(raw)
        except (ValueError, TypeError):
            continue

        # Upsert manual override row
        existing = PlotExtractedData.query.filter_by(
            analysis_id=analysis.id, field_name=field_name, source='manual'
        ).first()
        if existing:
            existing.value = str(val)
            existing.normalized_value = str(val)
            existing.unit = PLOT_FIELD_UNITS.get(field_name) or None
            existing.confidence = -1.0
            existing.is_manual_override = True
        else:
            row = PlotExtractedData(
                analysis_id=analysis.id,
                field_name=field_name,
                value=str(val),
                normalized_value=str(val),
                unit=PLOT_FIELD_UNITS.get(field_name) or None,
                confidence=-1.0,
                is_manual_override=True,
                source='manual',
            )
            db.session.add(row)
        saved[field_name] = val

    db.session.commit()
    return jsonify({'ok': True, 'saved': saved,
                    'road_direction': analysis.road_direction,
                    'authority': analysis.authority})


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/validation')
@login_required
def validation(project_id, analysis_id):
    """
    Detect conflicts between values for the same field from different document
    sources. A conflict exists when sources differ by more than 5%.
    """
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)

    import json as _json

    conflict_rows = []
    conflict_count = 0
    field_summaries = []

    for field_name in PLOT_FIELD_NAMES:
        rows = PlotExtractedData.query.filter_by(
            analysis_id=analysis.id, field_name=field_name
        ).filter(PlotExtractedData.source.isnot(None)).all()

        # Group by source, skip manual overrides for conflict detection
        by_source = {}
        for r in rows:
            if r.source and r.source != 'manual' and r.float_value is not None:
                by_source[r.source] = r.float_value

        has_conflict = False
        mismatch_pct = 0.0
        values_json = {}

        if len(by_source) >= 2:
            vals = list(by_source.values())
            mn, mx = min(vals), max(vals)
            if mn > 0:
                mismatch_pct = round((mx - mn) / mn * 100, 1)
                if mismatch_pct > 5.0:
                    has_conflict = True
                    conflict_count += 1
                    values_json = by_source

                    # Persist conflict (upsert)
                    conflict = PlotConflict.query.filter_by(
                        analysis_id=analysis.id, field_name=field_name
                    ).first()
                    if not conflict:
                        conflict = PlotConflict(
                            analysis_id=analysis.id,
                            field_name=field_name,
                            conflict_type='mismatch',
                        )
                        db.session.add(conflict)
                    conflict.values_json = _json.dumps(values_json)
                    conflict.mismatch_pct = mismatch_pct
                    conflict.suggested = max(by_source, key=lambda s: _SOURCE_PRIORITY.get(s, 99) * -1)
                    conflict.resolved = False
                    conflict_rows.append(conflict)

        field_summaries.append({
            'field': field_name,
            'conflict': has_conflict,
            'mismatch_pct': mismatch_pct,
            'by_source': by_source,
        })

    db.session.commit()

    return jsonify({
        'ok': True,
        'analysis_id': analysis.id,
        'has_conflicts': conflict_count > 0,
        'conflict_count': conflict_count,
        'fields': field_summaries,
    })


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/resolve', methods=['POST'])
@login_required
def resolve(project_id, analysis_id):
    """
    Resolve a detected conflict by choosing a source of truth or entering a
    custom value. Stores the resolution in PlotConflict.
    """
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)

    data = request.get_json(silent=True) or request.form
    field_name = (data.get('field_name') or '').strip()
    source = (data.get('source') or '').strip()
    custom_value = data.get('custom_value')

    if not field_name:
        return jsonify({'ok': False, 'error': 'field_name is required'}), 400

    if source == 'custom':
        if custom_value in (None, ''):
            return jsonify({'ok': False, 'error': 'custom_value required when source=custom'}), 400
        try:
            resolved_val = str(float(custom_value))
        except (ValueError, TypeError):
            return jsonify({'ok': False, 'error': 'custom_value must be a number'}), 400
    else:
        # Find the row for this source
        row = PlotExtractedData.query.filter_by(
            analysis_id=analysis.id, field_name=field_name, source=source
        ).first()
        if not row or row.float_value is None:
            return jsonify({'ok': False, 'error': f'No data for source={source}, field={field_name}'}), 404
        resolved_val = str(row.float_value)

    # Upsert PlotConflict
    conflict = PlotConflict.query.filter_by(
        analysis_id=analysis.id, field_name=field_name
    ).first()
    if not conflict:
        conflict = PlotConflict(
            analysis_id=analysis.id, field_name=field_name, conflict_type='mismatch'
        )
        db.session.add(conflict)
    conflict.resolved = True
    conflict.resolved_source = source
    conflict.resolved_value = resolved_val
    db.session.commit()

    return jsonify({
        'ok': True,
        'field_name': field_name,
        'resolved_source': source,
        'resolved_value': resolved_val,
    })


@bp.route('/projects/<int:project_id>/plot-analysis/<int:analysis_id>/compute', methods=['POST'])
@login_required
def compute_analysis(project_id, analysis_id):
    """
    Run compliance rules on an existing PlotAnalysis using its extracted data.
    Returns per-field results in the legacy naming scheme.
    """
    require_architect()
    project, analysis = _analysis_or_404(project_id, analysis_id)

    # Build field_map from extracted data, honouring conflict resolutions.
    field_map = _load_input_payload(analysis)
    for field_name in PLOT_FIELD_NAMES:
        # Check if there's a resolved conflict for this field
        conflict = PlotConflict.query.filter_by(
            analysis_id=analysis.id, field_name=field_name, resolved=True
        ).first()
        if conflict and conflict.resolved_value:
            try:
                field_map[field_name] = float(conflict.resolved_value)
                continue
            except (ValueError, TypeError):
                pass

        best = _best_field_value(analysis.id, field_name)
        if best and best.float_value is not None:
            field_map[field_name] = best.float_value

    if not field_map:
        return jsonify({'ok': False, 'error': 'No extracted data available. Upload a drawing first.'}), 400

    result = check_compliance(field_map)
    _save_analysis(project.id, field_map, result, analysis=analysis)
    db.session.commit()

    result_payload = _load_result_payload(analysis) or {}
    return jsonify({
        'ok': True,
        'analysis_id': analysis.id,
        'overall_score': analysis.overall_score,
        'completeness': analysis.data_completeness,
        'trust_level': analysis.trust_level,
        'results': result_payload.get('rule_results') or [],
        'field_map': field_map,
    })


@bp.route('/projects/<int:project_id>/plot-analysis/client-view')
@login_required
def client_view(project_id):
    if current_user.role != 'client':
        abort(403)
    project = Project.query.get_or_404(project_id)
    if project.client_id != current_user.id:
        abort(403)

    analyses = (
        PlotAnalysis.query
        .filter_by(project_id=project.id)
        .filter(PlotAnalysis.status.in_(['confirmed', 'analyzed', 'insufficient_data']))
        .order_by(PlotAnalysis.created_at.desc())
        .all()
    )
    return render_template(
        'client/plot_analysis.html',
        project=project,
        analyses=analyses,
        active_project=project,
        active_tab='plot',
    )


@bp.route('/my_project/plot-analysis')
@login_required
def client_view_legacy():
    if current_user.role != 'client':
        abort(403)
    from app.routes.client import get_client_project
    project = get_client_project()
    if not project:
        return render_template('client/no_project.html')
    return redirect(url_for('plot_analysis.client_view', project_id=project.id))
