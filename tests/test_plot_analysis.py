"""
Tests for the Plot Analysis Module.

Covers:
  1.  Upload works and creates PlotAnalysis + PlotExtractedData records
  2.  OCR fallback: missing fields left empty, not crashed
  3.  Editing fields saves correctly with is_manual_override=True
  4.  Empty field value clears the value (not stored as 'None')
  5.  Rule engine — correct pass / warning / fail classification
  6.  Rule engine — FSI (built-up area) check
  7.  Trust-aware score — sparse data is completeness-penalized
  8.  Client view restricted: non-client cannot access /my_project/plot-analysis
  9.  Client sees simplified view (no edit UI)
  10. Report page renders for analyzed analysis
"""

import io
import json
import os
import pytest

from app import create_app, db
from app.models import User, Project, PlotAnalysis, PlotExtractedData, PlotComplianceResult
from werkzeug.security import generate_password_hash


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path):
    upload_folder = str(tmp_path / 'uploads')
    os.makedirs(upload_folder, exist_ok=True)
    application = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test-plot-analysis',
        'UPLOAD_FOLDER': upload_folder,
    })
    with application.app_context():
        db.create_all()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def architect(app):
    with app.app_context():
        u = User(name='Test Architect', email='arch@test.com', role='architect',
                 password_hash=generate_password_hash('pass123'))
        db.session.add(u)
        db.session.commit()
        return User.query.get(u.id)


@pytest.fixture
def client_user(app):
    with app.app_context():
        u = User(name='Test Client', email='client@test.com', role='client',
                 password_hash=generate_password_hash('pass123'))
        db.session.add(u)
        db.session.commit()
        return User.query.get(u.id)


@pytest.fixture
def project(app, architect, client_user):
    with app.app_context():
        p = Project(name='Test Plot Project', architect_id=architect.id,
                    client_id=client_user.id, status='Design', plot_zone='Residential')
        db.session.add(p)
        db.session.commit()
        return Project.query.get(p.id)


def _login(test_client, email, password='pass123'):
    return test_client.post('/login', data={'email': email, 'password': password},
                            follow_redirects=True)


def _make_png_bytes():
    """Return minimal valid PNG bytes (1×1 white pixel)."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (1, 1), color=(255, 255, 255)).save(buf, format='PNG')
    return buf.getvalue()


def _plot_result(fields):
    from app.routes.plot_analysis import _normalize_analysis_result
    from app.tn_engine import get_engine

    normalized = _normalize_analysis_result(fields, get_engine().check_compliance(fields))
    return (
        normalized['rule_results'],
        normalized['overall_score'],
        normalized['data_completeness'],
        normalized['trust_level'],
    )


# ── 1. Upload creates records ─────────────────────────────────────────────

def test_upload_creates_analysis(client, app, architect, project):
    _login(client, architect.email)
    data = {
        'drawing': (io.BytesIO(_make_png_bytes()), 'drawing.png', 'image/png'),
    }
    with app.app_context():
        pid = Project.query.filter_by(name='Test Plot Project').first().id

    resp = client.post(f'/projects/{pid}/plot-analysis/upload',
                       data=data, content_type='multipart/form-data',
                       follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        from app.models import PlotDocument
        a = PlotAnalysis.query.filter_by(project_id=pid).first()
        assert a is not None, 'PlotAnalysis record should be created'
        # A PlotDocument record should be created for the uploaded file
        doc = PlotDocument.query.filter_by(analysis_id=a.id).first()
        assert doc is not None, 'PlotDocument record should be created'
        assert doc.original_filename == 'drawing.png'
        # Status: 'extracted' if OCR ran (even with 0 fields), 'pending' if OCR crashed
        assert a.status in ('extracted', 'pending')


# ── 2. OCR failure handled gracefully ────────────────────────────────────

def test_ocr_fallback_no_crash(app):
    from app.ocr import extract_dimensions
    result = extract_dimensions('/nonexistent/path/image.png')
    # Should return a dict with None values, not raise
    assert isinstance(result, dict)
    for key in ['area', 'frontage']:
        assert key in result
        assert result[key].get('value') is None or isinstance(result[key].get('value'), (int, float, type(None)))


# ── 3. Edit saves with manual override ───────────────────────────────────

def test_edit_saves_manual_override(client, app, architect, project):
    _login(client, architect.email)

    with app.app_context():
        pid = Project.query.filter_by(name='Test Plot Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=architect.id,
                         file_path='dummy.png', original_filename='dummy.png')
        db.session.add(a)
        db.session.flush()
        for field_key, _label, unit in [('plot_area', 'Plot Area', 'sq.m'), ('frontage', 'Frontage', 'm')]:
            db.session.add(PlotExtractedData(analysis_id=a.id, field_name=field_key,
                                              value=None, unit=unit, confidence=0.0))
        db.session.commit()
        aid = a.id

    resp = client.post(f'/projects/{pid}/plot-analysis/{aid}/edit',
                       data={'plot_area': '800', 'frontage': '25', 'road_width': '7',
                             'height_m': '', 'fsi_proposed': '', 'coverage_pct': '',
                             'front_setback_m': '', 'rear_setback_m': '',
                             'side_setback_m': ''},
                       follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        area_field = PlotExtractedData.query.filter_by(
            analysis_id=aid, field_name='plot_area', source='manual'
        ).first()
        assert area_field.value == '800'
        assert area_field.is_manual_override is True


# ── 4. Empty field clears value ───────────────────────────────────────────

def test_edit_empty_clears_value(client, app, architect, project):
    _login(client, architect.email)

    with app.app_context():
        pid = Project.query.filter_by(name='Test Plot Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=architect.id,
                         file_path='dummy.png', original_filename='dummy.png')
        db.session.add(a)
        db.session.flush()
        f = PlotExtractedData(analysis_id=a.id, field_name='plot_area',
                              value='900', unit='sq.m', confidence=0.85)
        db.session.add(f)
        db.session.commit()
        aid = a.id

    client.post(f'/projects/{pid}/plot-analysis/{aid}/edit',
                data={'plot_area': '', 'frontage': '', 'road_width': '',
                      'front_setback_m': '', 'rear_setback_m': '',
                      'side_setback_m': '', 'height_m': '', 'fsi_proposed': '',
                      'coverage_pct': ''},
                follow_redirects=True)

    with app.app_context():
        area_field = PlotExtractedData.query.filter_by(
            analysis_id=aid, field_name='plot_area', source='manual'
        ).first()
        assert area_field is None


# ── 5. Rule engine — pass / warning / fail ────────────────────────────────

def test_rule_engine_pass_warning_fail():
    field_map = {
        'location_type': 'corporation',
        'building_type': 'residential',
        'plot_area': 700.0,
        'frontage': 3.0,
        'road_width': 7.0,
        'height_m': 7.0,
        'fsi_proposed': 1.2,
        'coverage_pct': 50.0,
        'front_setback_m': 3.0,
        'rear_setback_m': 1.5,
        'side_setback_m': 1.5,
    }
    results, overall, completeness, trust_level = _plot_result(field_map)

    by_rule = {r['rule_name']: r for r in results}

    assert by_rule['plot_area']['status'] == 'pass'
    assert by_rule['frontage']['status'] == 'fail'
    assert overall < 100.0
    assert completeness == 100.0
    assert trust_level == 'FULL'


# ── 6. Rule engine — FSI check ────────────────────────────────────────────

def test_rule_engine_fsi_fail():
    field_map = {
        'location_type': 'corporation',
        'building_type': 'residential',
        'plot_area': 500.0,
        'frontage': 10.0,
        'road_width': 7.0,
        'height_m': 7.0,
        'fsi_proposed': 2.5,
        'coverage_pct': 50.0,
        'front_setback_m': 3.0,
        'rear_setback_m': 1.5,
        'side_setback_m': 1.5,
    }
    results, _, _, _ = _plot_result(field_map)
    by_rule = {r['rule_name']: r for r in results}
    assert by_rule['fsi_proposed']['status'] == 'fail'


# ── 7. Overall fuzzy score ────────────────────────────────────────────────

def test_score_all_pass():
    field_map = {
        'location_type': 'corporation',
        'building_type': 'residential',
        'plot_area': 100.0,
        'frontage': 10.0,
        'road_width': 12.0,
        'height_m': 7.0,
        'fsi_proposed': 1.5,
        'coverage_pct': 50.0,
        'front_setback_m': 3.0,
        'rear_setback_m': 1.5,
        'side_setback_m': 1.5,
    }
    results, overall, completeness, trust_level = _plot_result(field_map)
    assert overall >= 85.0, f'All-pass should score >= 85, got {overall}'
    assert completeness == 100.0
    assert trust_level == 'FULL'


def test_score_all_fail():
    field_map = {
        'location_type': 'corporation',
        'building_type': 'residential',
        'plot_area': 65.0,
        'frontage': 3.0,
        'road_width': 7.0,
        'height_m': 12.0,
        'fsi_proposed': 2.5,
        'coverage_pct': 90.0,
        'front_setback_m': 1.0,
        'rear_setback_m': 0.5,
        'side_setback_m': 0.5,
    }
    _results, overall, completeness, trust_level = _plot_result(field_map)
    assert overall < 60.0, f'All-fail should score < 60, got {overall}'
    assert completeness == 100.0
    assert trust_level == 'FULL'


def test_sparse_score_is_penalized_by_completeness():
    _results, overall, completeness, trust_level = _plot_result({
        'location_type': 'corporation',
        'building_type': 'residential',
        'plot_area': 1000.0,
    })
    assert completeness == 0.0
    assert overall == 0.0
    assert trust_level == 'INSUFFICIENT'


# ── 8. Client view restricted ────────────────────────────────────────────

def test_client_view_blocked_for_architect(client, app, architect, project):
    _login(client, architect.email)
    resp = client.get('/my_project/plot-analysis', follow_redirects=False)
    assert resp.status_code in (302, 403)


# ── 9. Client sees no edit UI ─────────────────────────────────────────────

def test_client_sees_simplified_view(client, app, client_user, project):
    _login(client, client_user.email)

    with app.app_context():
        # Assign session project for client portal
        proj = Project.query.filter_by(name='Test Plot Project').first()

    # Seed session so get_client_project() finds the project
    with client.session_transaction() as sess:
        sess['selected_project_id'] = proj.id if project else None

    resp = client.get('/my_project/plot-analysis', follow_redirects=True)
    assert resp.status_code == 200
    # Should NOT contain edit/run-analysis form actions
    assert b'run-analysis' not in resp.data


# ── 10. Report renders for analyzed analysis ──────────────────────────────

def test_report_renders(client, app, architect, project):
    _login(client, architect.email)

    with app.app_context():
        pid = Project.query.filter_by(name='Test Plot Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=architect.id,
                         file_path='dummy.png', original_filename='test.png',
                         overall_score=78.5, status='analyzed',
                         data_completeness_score=66.7, trust_level='PROVISIONAL')
        db.session.add(a)
        db.session.flush()
        db.session.add(PlotComplianceResult(
            analysis_id=a.id, rule_name='area', rule_label='Minimum Plot Area',
            expected_value='600 sq ft', actual_value='700 sq ft',
            score=100.0, status='pass', suggestion='—',
        ))
        db.session.commit()
        aid = a.id

    resp = client.get(f'/projects/{pid}/plot-analysis/{aid}/report')
    assert resp.status_code == 200
    assert b'Plot Compliance Report' in resp.data
    assert b'TNPCR' in resp.data
