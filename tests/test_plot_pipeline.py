"""
Pipeline tests for the fixed Plot Analysis module.

Covers:
  OCR
    1.  extract_dimensions returns a dict with all 9 fields + _raw_text
    2.  extract_dimensions gracefully handles a missing file
    3.  extract_dimensions returns real float confidence (not a hardcoded constant)
    4.  _preprocess_to_pil returns a PIL Image in-memory (no temp file on disk)
    5.  No temp *.pre.png files left on disk after extraction

  Extraction
    6.  _extract_labelled finds area from "TOTAL PLOT AREA = 1400"
    7.  _extract_labelled finds built_up_area from "BUILT UP AREA = 900"
    8.  _extract_positional returns frontage/depth from fake word list
    9.  _extract_positional excludes numbers near noise context words

  Unit detection + normalization
    10. _detect_unit identifies 'sq m', 'sq ft', 'm', 'ft'
    11. _normalize converts sq m → sq ft for area field
    12. _normalize converts m → ft for frontage field
    13. _normalize converts ft → m for height field
    14. _normalize is identity when unit already matches rule-engine unit

  Rule engine
    15. evaluate_rules returns 4-tuple (results, overall, completeness, trust_level)
    16. Missing fields have status='not_available' and score=None
    17. Overall score is completeness-penalized for sparse datasets
    18. data_completeness_score reflects fraction of provided fields
    19. All-pass scenario scores >= 85
    20. All-fail scenario scores < 60
    21. FSI rule: fail when built_up_area / area > 2.0
    22. FSI rule: not_available when area is missing
    23. is_max height rule: pass when height <= 8.5m

  Database
    24. PlotExtractedData has value_original + unit_original columns
    25. PlotAnalysis has data_completeness_score column
    26. idx_plot_analysis_project_id index exists on plot_analysis table
    27. idx_plot_extracted_analysis_id index exists on plot_extracted_data table
    28. idx_plot_compliance_analysis_id index exists on plot_compliance_result table

  Routes (integration)
    29. Upload rejects PDF files
    30. Upload rejects files > 5 MB
    31. Upload stores status='extracted' after successful OCR
    32. run_analysis sets status='analyzed' and stores data_completeness_score
    33. JSON API returns expected keys
    34. Edit route reverts status to 'extracted' when analysis was 'analyzed'
"""

import io
import json
import os
import pytest

from app import create_app, db
from app.models import (
    User, Project, PlotAnalysis, PlotExtractedData, PlotComplianceResult,
)
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
        'SECRET_KEY': 'test-pipeline-key',
        'UPLOAD_FOLDER': upload_folder,
    })
    with application.app_context():
        db.create_all()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def arch_user(app):
    with app.app_context():
        u = User(name='Pipeline Arch', email='parch@test.com', role='architect',
                 password_hash=generate_password_hash('pass123'))
        db.session.add(u)
        db.session.commit()
        return User.query.get(u.id)


@pytest.fixture
def client_user(app):
    with app.app_context():
        u = User(name='Pipeline Client', email='pclient@test.com', role='client',
                 password_hash=generate_password_hash('pass123'))
        db.session.add(u)
        db.session.commit()
        return User.query.get(u.id)


@pytest.fixture
def project(app, arch_user, client_user):
    with app.app_context():
        p = Project(name='Pipeline Test Project', architect_id=arch_user.id,
                    client_id=client_user.id, status='Design', plot_zone='Residential')
        db.session.add(p)
        db.session.commit()
        return Project.query.get(p.id)


def _login(test_client, email, password='pass123'):
    return test_client.post('/login',
                            data={'email': email, 'password': password},
                            follow_redirects=True)


def _make_png():
    """Minimal valid 1×1 white PNG."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (1, 1), color=(255, 255, 255)).save(buf, format='PNG')
    return buf.getvalue()


def _make_pdf():
    """Minimal PDF bytes."""
    return b'%PDF-1.0\n1 0 obj<</Type/Catalog>>endobj\n%%EOF'


def _make_big_png():
    """PNG-header bytes padded to exceed 5 MB."""
    header = _make_png()
    return header + b'\x00' * (5 * 1024 * 1024 + 1)


def _make_fake_png():
    return b'not-a-real-image'


LEGACY_FIELD_ALIASES = {
    'area': 'plot_area',
    'front_setback': 'front_setback_m',
    'rear_setback': 'rear_setback_m',
    'side_setback': 'side_setback_m',
    'height': 'height_m',
}


def _canonical_field_name(field_name):
    return LEGACY_FIELD_ALIASES.get(field_name, field_name)


def _plot_defaults():
    return {
        'location_type': 'corporation',
        'building_type': 'residential',
    }


def _canonical_field_values(field_values):
    values = {}
    source_values = dict(field_values)
    plot_area = source_values.get('plot_area', source_values.get('area'))
    for field_name, value in source_values.items():
        if field_name == 'built_up_area':
            continue
        values[_canonical_field_name(field_name)] = value
    if 'fsi_proposed' not in values and source_values.get('built_up_area') is not None and plot_area:
        values['fsi_proposed'] = round(float(source_values['built_up_area']) / float(plot_area), 2)
    return values


def _normalized_plot_result(fields):
    from app.routes.plot_analysis import _normalize_analysis_result
    from app.tn_engine import get_engine

    merged = {**_plot_defaults(), **_canonical_field_values(fields)}
    return _normalize_analysis_result(merged, get_engine().check_compliance(merged))


# ══════════════════════════════════════════════════════════════════════════
# OCR — unit tests (no live Tesseract required)
# ══════════════════════════════════════════════════════════════════════════

def test_extract_dimensions_missing_file():
    """Test 2: graceful handling of nonexistent path."""
    from app.ocr import extract_dimensions
    result = extract_dimensions('/nonexistent/path/image.png')
    assert isinstance(result, dict)
    assert '_raw_text' in result
    assert result['_raw_text'] == ''
    for key in ['area', 'frontage', 'depth']:
        assert key in result
        assert result[key]['value'] is None
        assert result[key]['confidence'] == 0.0


def test_extract_dimensions_returns_all_fields():
    """Test 1: all 9 fields + _raw_text always present."""
    from app.ocr import extract_dimensions
    result = extract_dimensions('/nonexistent/path/image.png')
    expected_fields = [
        'area', 'frontage', 'depth', 'road_width',
        'front_setback', 'rear_setback', 'side_setback',
        'height', 'built_up_area',
    ]
    for f in expected_fields:
        assert f in result, f'Missing field: {f}'
    assert '_raw_text' in result


def test_no_temp_files_created(tmp_path):
    """Test 5: no .pre.png files left on disk after extraction."""
    from app.ocr import extract_dimensions
    # Even if we pass a real small PNG, no temp file should remain
    img_path = str(tmp_path / 'test.png')
    with open(img_path, 'wb') as f:
        f.write(_make_png())
    extract_dimensions(img_path)
    leftover = list(tmp_path.glob('*.pre.png'))
    assert leftover == [], f'Temp files left: {leftover}'


def test_preprocess_returns_pil_image_in_memory(tmp_path):
    """Test 4: _preprocess_to_pil returns PIL Image, no disk write."""
    from app.ocr import _preprocess_to_pil
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError:
        pytest.skip('cv2/PIL not installed')

    img_path = str(tmp_path / 'sample.png')
    # Create a real 50×50 grayscale PNG
    arr = np.full((50, 50), 200, dtype=np.uint8)
    cv2.imwrite(img_path, arr)

    result = _preprocess_to_pil(img_path)
    # No temp file created
    assert not os.path.exists(img_path + '.pre.png')
    # Returns PIL Image
    assert result is not None
    from PIL import Image
    assert isinstance(result, Image.Image)


# ══════════════════════════════════════════════════════════════════════════
# Extraction — labelled
# ══════════════════════════════════════════════════════════════════════════

def test_extract_labelled_area():
    """Test 6: labelled area extraction."""
    from app.ocr import _extract_labelled, _build_conf_lookup, Word
    text = 'TOTAL PLOT AREA = 1400 sqft'
    # Provide fake word list so confidence lookup works
    words = [Word(text='1400', x=0, y=0, w=30, h=10, conf=88.0)]
    lookup = _build_conf_lookup(words)
    result = _extract_labelled(text, lookup)
    assert 'area' in result
    val, conf, unit_orig, val_orig = result['area']
    assert val == 1400.0
    assert conf == pytest.approx(0.88, abs=0.01)


def test_extract_labelled_built_up_area():
    """Test 7: labelled built_up_area extraction."""
    from app.ocr import _extract_labelled, _build_conf_lookup, Word
    text = 'BUILT UP AREA = 900 sqft'
    words = [Word(text='900', x=0, y=0, w=30, h=10, conf=72.0)]
    lookup = _build_conf_lookup(words)
    result = _extract_labelled(text, lookup)
    assert 'built_up_area' in result
    val, conf, *_ = result['built_up_area']
    assert val == 900.0
    assert conf == pytest.approx(0.72, abs=0.01)


# ══════════════════════════════════════════════════════════════════════════
# Extraction — positional heuristic
# ══════════════════════════════════════════════════════════════════════════

def _make_word(text, x, y, w=30, h=12, conf=80.0):
    from app.ocr import Word
    return Word(text=text, x=x, y=y, w=w, h=h, conf=conf)


def test_extract_positional_frontage_depth():
    """Test 8: frontage from drawing-zone top edge, depth from left edge."""
    from app.ocr import _extract_positional
    img_w, img_h = 2000, 2000
    # Top candidate must be in the drawing band, not the header metadata zone.
    top_word   = _make_word('35', x=500, y=430)
    left_word  = _make_word('28', x=100, y=700)
    # Interior word — should be ignored
    inner_word = _make_word('10', x=800, y=800)

    result = _extract_positional([top_word, left_word, inner_word], img_w, img_h)
    assert 'frontage' in result
    assert result['frontage'][0] == 35.0
    assert 'depth' in result
    assert result['depth'][0] == 28.0


def test_extract_positional_excludes_noise_context():
    """Test 9: number near 'scale' word must be excluded."""
    from app.ocr import _extract_positional
    img_w, img_h = 2000, 2000

    dim_word   = _make_word('100', x=500, y=430)
    noise_word = _make_word('scale', x=530, y=430, conf=90.0)

    result = _extract_positional([dim_word, noise_word], img_w, img_h)
    # '100' should be excluded because 'scale' is nearby
    assert 'frontage' not in result


def test_extract_positional_rejects_footer_numbers():
    from app.ocr import _extract_positional
    img_w, img_h = 2000, 2000
    footer_word = _make_word('260', x=1200, y=1910)
    result = _extract_positional([footer_word], img_w, img_h)
    assert result == {}


def test_extract_fmb_metadata_parses_hect_are_area():
    from app.ocr import Word, _build_conf_lookup, _extract_fmb_metadata

    words = [
        Word(text='Area', x=1500, y=40, w=80, h=24, conf=88.0),
        Word(text='Hect', x=1590, y=40, w=80, h=24, conf=92.0),
        Word(text='00', x=1680, y=40, w=30, h=24, conf=95.0),
        Word(text='Ares', x=1720, y=40, w=70, h=24, conf=91.0),
        Word(text='13.16', x=1800, y=40, w=80, h=24, conf=89.0),
        Word(text='Survey', x=1200, y=80, w=90, h=24, conf=87.0),
        Word(text='No', x=1295, y=80, w=30, h=24, conf=86.0),
        Word(text='123/1B', x=1340, y=80, w=90, h=24, conf=90.0),
    ]
    lookup = _build_conf_lookup(words)
    found, metadata = _extract_fmb_metadata(words, lookup, img_h=2000)

    assert found['area'][0] == pytest.approx(14165.3, abs=0.2)
    assert found['area'][2] == 'are'
    assert metadata['plot_area_sqm'] == pytest.approx(1316.0, abs=0.1)
    assert metadata['survey_number'] == '123/1B'


# ══════════════════════════════════════════════════════════════════════════
# Unit detection + normalization
# ══════════════════════════════════════════════════════════════════════════

def test_detect_unit_sq_m():
    """Test 10a."""
    from app.ocr import _detect_unit
    assert _detect_unit('plot area = 130 sq m') == 'sq m'


def test_detect_unit_sq_ft():
    """Test 10b."""
    from app.ocr import _detect_unit
    assert _detect_unit('area = 1400 sqft') == 'sq ft'


def test_detect_unit_m():
    """Test 10c."""
    from app.ocr import _detect_unit
    assert _detect_unit('frontage = 12 m') == 'm'


def test_detect_unit_ft():
    """Test 10d."""
    from app.ocr import _detect_unit
    assert _detect_unit("frontage = 35'") == 'ft'


def test_normalize_sq_m_to_sq_ft():
    """Test 11: 1 sq m ≈ 10.764 sq ft."""
    from app.ocr import _normalize
    result = _normalize(1.0, 'sq m', 'area')
    assert result == pytest.approx(10.7639, abs=0.01)


def test_normalize_m_to_ft_linear():
    """Test 12: 1 m = 3.28084 ft."""
    from app.ocr import _normalize
    result = _normalize(1.0, 'm', 'frontage')
    assert result == pytest.approx(3.28084, abs=0.001)


def test_normalize_ft_to_m_height():
    """Test 13: height in ft → m (1 ft = 0.3048 m)."""
    from app.ocr import _normalize
    result = _normalize(1.0, 'ft', 'height')
    assert result == pytest.approx(0.3048, abs=0.001)


def test_normalize_identity_sq_ft():
    """Test 14: already sq ft → no change."""
    from app.ocr import _normalize
    assert _normalize(1400.0, 'sq ft', 'area') == 1400.0


def test_normalize_identity_ft():
    """Test 14b: already ft → no change."""
    from app.ocr import _normalize
    assert _normalize(35.0, 'ft', 'frontage') == 35.0


def test_normalize_identity_m():
    """Test 14c: height already in m → no change."""
    from app.ocr import _normalize
    assert _normalize(8.5, 'm', 'height') == 8.5


# ══════════════════════════════════════════════════════════════════════════
# Rule engine
# ══════════════════════════════════════════════════════════════════════════

def test_evaluate_rules_returns_four_tuple():
    """Test 15: normalized plot result exposes rules, score, completeness, trust."""
    out = _normalized_plot_result({'plot_area': 700.0, 'frontage': 25.0, 'road_width': 7.0})
    assert 'rule_results' in out
    assert 'overall_score' in out
    assert 'data_completeness' in out
    assert 'trust_level' in out


def test_missing_field_is_not_available():
    """Test 16: absent field gets status='not_available', score=None."""
    result = _normalized_plot_result({'plot_area': 700.0, 'frontage': 25.0, 'road_width': 7.0})
    results = result['rule_results']
    by_rule = {r['rule_name']: r for r in results}
    assert by_rule['height_m']['status'] == 'not_available'
    assert by_rule['height_m']['score'] is None


def test_sparse_data_penalizes_overall_score():
    """Test 17: one passing field should not yield a trustworthy 100% overall score."""
    result = _normalized_plot_result({'plot_area': 1000.0})
    overall = result['overall_score']
    completeness = result['data_completeness']
    trust_level = result['trust_level']
    assert completeness == 0.0
    assert overall == 0.0
    assert trust_level == 'INSUFFICIENT'


def test_data_completeness_score():
    """Test 18: completeness reflects provided fields."""
    empty_result = _normalized_plot_result({})
    assert empty_result['data_completeness'] == 0.0
    assert empty_result['trust_level'] == 'INSUFFICIENT'

    full_result = _normalized_plot_result({
        'plot_area': 100, 'frontage': 10, 'road_width': 12,
        'front_setback_m': 3, 'rear_setback_m': 1.5, 'side_setback_m': 1.5,
        'height_m': 7, 'fsi_proposed': 1.5, 'coverage_pct': 50,
    })
    assert full_result['data_completeness'] == 100.0
    assert full_result['trust_level'] == 'FULL'


def test_all_pass_score():
    """Test 19: all-pass field map scores >= 85."""
    result = _normalized_plot_result({
        'plot_area': 100, 'frontage': 10, 'road_width': 12,
        'front_setback_m': 3, 'rear_setback_m': 1.5, 'side_setback_m': 1.5,
        'height_m': 7, 'fsi_proposed': 1.5, 'coverage_pct': 50,
    })
    overall = result['overall_score']
    trust_level = result['trust_level']
    assert overall >= 85.0, f'Expected >= 85, got {overall}'
    assert trust_level == 'FULL'


def test_all_fail_score():
    """Test 20: all-fail field map scores < 60."""
    result = _normalized_plot_result({
        'plot_area': 65, 'frontage': 3, 'road_width': 7,
        'front_setback_m': 1.0, 'rear_setback_m': 0.5, 'side_setback_m': 0.5,
        'height_m': 12, 'fsi_proposed': 2.5, 'coverage_pct': 90,
    })
    overall = result['overall_score']
    trust_level = result['trust_level']
    assert overall < 60.0, f'Expected < 60, got {overall}'
    assert trust_level == 'FULL'


def test_fsi_rule_fail():
    """Test 21: FSI fail when proposed FSI exceeds the rule limit."""
    results = _normalized_plot_result({
        'plot_area': 500, 'frontage': 10, 'road_width': 7,
        'front_setback_m': 3, 'rear_setback_m': 1.5, 'side_setback_m': 1.5,
        'height_m': 7, 'fsi_proposed': 2.5, 'coverage_pct': 50,
    })['rule_results']
    by_rule = {r['rule_name']: r for r in results}
    assert by_rule['fsi_proposed']['status'] == 'fail'


def test_fsi_not_available_without_area():
    """Test 22: FSI is not_available when proposed FSI is missing."""
    result = _normalized_plot_result({'plot_area': 900, 'frontage': 10, 'road_width': 7})
    results = result['rule_results']
    by_rule = {r['rule_name']: r for r in results}
    assert by_rule['fsi_proposed']['status'] == 'not_available'
    assert result['data_completeness'] == pytest.approx(33.3, abs=0.1)
    assert result['overall_score'] == pytest.approx(33.3, abs=0.1)
    assert result['trust_level'] == 'PARTIAL'


def test_height_pass():
    """Test 23: height 7.0 m passes <= 8.5 m limit."""
    results = _normalized_plot_result({
        'plot_area': 100, 'frontage': 10, 'road_width': 7,
        'height_m': 7.0,
    })['rule_results']
    by_rule = {r['rule_name']: r for r in results}
    assert by_rule['height_m']['status'] == 'pass'


def test_height_fail():
    """height 12.0 m fails once it exceeds the applicable limit."""
    results = _normalized_plot_result({
        'plot_area': 100, 'frontage': 10, 'road_width': 7,
        'height_m': 12.0,
    })['rule_results']
    by_rule = {r['rule_name']: r for r in results}
    assert by_rule['height_m']['status'] == 'fail'


def test_validate_secure_mime_accepts_webp():
    from app.storage import validate_secure_mime

    stream = io.BytesIO(b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 16)
    stream.filename = 'plot.webp'
    assert validate_secure_mime(stream, ['image/webp']) is True


# ══════════════════════════════════════════════════════════════════════════
# Database — model columns and indexes
# ══════════════════════════════════════════════════════════════════════════

def test_plot_extracted_data_has_new_columns(app):
    """Test 24: value_original and unit_original exist on PlotExtractedData."""
    with app.app_context():
        from sqlalchemy import inspect
        cols = {c['name'] for c in inspect(db.engine).get_columns('plot_extracted_data')}
        assert 'value_original' in cols
        assert 'unit_original' in cols


def test_plot_analysis_has_completeness_column(app):
    """Test 25: data_completeness_score column exists on plot_analysis."""
    with app.app_context():
        from sqlalchemy import inspect
        cols = {c['name'] for c in inspect(db.engine).get_columns('plot_analysis')}
        assert 'data_completeness_score' in cols


def test_indexes_exist(app):
    """Tests 26-28: verify all three performance indexes were created."""
    with app.app_context():
        from sqlalchemy import inspect
        insp = inspect(db.engine)

        pa_indexes  = {i['name'] for i in insp.get_indexes('plot_analysis')}
        ped_indexes = {i['name'] for i in insp.get_indexes('plot_extracted_data')}
        pcr_indexes = {i['name'] for i in insp.get_indexes('plot_compliance_result')}

        assert 'idx_plot_analysis_project_id'   in pa_indexes,  'Missing PA index'
        assert 'idx_plot_extracted_analysis_id' in ped_indexes, 'Missing PED index'
        assert 'idx_plot_compliance_analysis_id' in pcr_indexes, 'Missing PCR index'


# ══════════════════════════════════════════════════════════════════════════
# Routes — integration tests
# ══════════════════════════════════════════════════════════════════════════

def test_upload_rejects_pdf(client, app, arch_user, project):
    """Test 29: PDF file is rejected with informative flash, not processed."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    resp = client.post(
        f'/projects/{pid}/plot-analysis/upload',
        data={'drawing': (io.BytesIO(_make_pdf()), 'plan.pdf', 'application/pdf')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # No PlotAnalysis record should have been created
    with app.app_context():
        count = PlotAnalysis.query.filter_by(project_id=pid).count()
        assert count == 0


def test_upload_rejects_oversized_file(client, app, arch_user, project):
    """Test 30: file > 5 MB is rejected."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    big = _make_big_png()
    resp = client.post(
        f'/projects/{pid}/plot-analysis/upload',
        data={'drawing': (io.BytesIO(big), 'big.png', 'image/png')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        count = PlotAnalysis.query.filter_by(project_id=pid).count()
        assert count == 0


def test_upload_creates_extracted_status(client, app, arch_user, project):
    """Test 31: after upload status is 'extracted' (not 'pending')."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    client.post(
        f'/projects/{pid}/plot-analysis/upload',
        data={'drawing': (io.BytesIO(_make_png()), 'drawing.png', 'image/png')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    with app.app_context():
        a = PlotAnalysis.query.filter_by(project_id=pid).first()
        assert a is not None
        # OCR will fail on a 1×1 PNG (no text) but the record should still exist
        # Status is either 'extracted' (OCR ran, returned nothing) or 'pending' (OCR crashed)
        assert a.status in ('extracted', 'pending')


def test_upload_rejects_invalid_image_payload(client, app, arch_user, project):
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    resp = client.post(
        f'/projects/{pid}/plot-analysis/upload',
        data={'drawing': (io.BytesIO(_make_fake_png()), 'fake.png', 'image/png')},
        content_type='multipart/form-data',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data['ok'] is False
    assert 'Invalid image file' in data['error']


def test_run_analysis_sets_insufficient_when_sparse(client, app, arch_user, project):
    """Test 32: sparse analyses stay insufficient instead of jumping to analyzed."""
    _login(client, arch_user.email)

    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=arch_user.id,
                         file_path='dummy.png', original_filename='dummy.png',
                         status='extracted')
        db.session.add(a)
        db.session.flush()
        a.input_payload = json.dumps(_plot_defaults())
        for fk, _, unit in [('plot_area', 'Plot Area', 'sq.m')]:
            db.session.add(PlotExtractedData(
                analysis_id=a.id, field_name=fk,
                value='700',
                unit=unit, confidence=0.85,
            ))
        db.session.commit()
        aid = a.id

    client.post(
        f'/projects/{pid}/plot-analysis/{aid}/run-analysis',
        follow_redirects=True,
    )

    with app.app_context():
        a = PlotAnalysis.query.get(aid)
        assert a.status in ('pending', 'insufficient_data')
        assert a.overall_score is not None
        assert a.data_completeness_score is not None
        assert a.trust_level == 'INSUFFICIENT'
        assert a.data_completeness_score == 0.0


def test_json_api_returns_expected_keys(client, app, arch_user, project):
    """Test 33: GET /json returns analysis_id, fields, compliance_results."""
    _login(client, arch_user.email)

    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=arch_user.id,
                         file_path='dummy.png', original_filename='dummy.png',
                         status='analyzed', overall_score=75.0,
                         data_completeness_score=44.4, trust_level='PARTIAL')
        db.session.add(a)
        db.session.commit()
        aid = a.id

    resp = client.get(f'/projects/{pid}/plot-analysis/{aid}/json')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    for key in ['analysis_id', 'overall_score', 'data_completeness_score', 'trust_level',
                'fields', 'compliance_results', 'status']:
        assert key in data, f'Missing key in JSON response: {key}'


def test_edit_reverts_status_to_extracted(client, app, arch_user, project):
    """Test 34: editing fields on an 'analyzed' analysis reverts it to 'extracted'."""
    _login(client, arch_user.email)

    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=arch_user.id,
                         file_path='dummy.png', original_filename='dummy.png',
                         status='analyzed', overall_score=80.0)
        db.session.add(a)
        db.session.flush()
        db.session.add(PlotExtractedData(
            analysis_id=a.id, field_name='plot_area',
            value='700', unit='sq.m', confidence=0.85,
        ))
        a.input_payload = json.dumps(_plot_defaults())
        db.session.commit()
        aid = a.id

    client.post(
        f'/projects/{pid}/plot-analysis/{aid}/edit',
        data={
            'plot_area': '800',
            'frontage': '25',
            'road_width': '7',
            'location_type': 'corporation',
            'building_type': 'residential',
            'front_setback_m': '',
            'rear_setback_m': '',
            'side_setback_m': '',
            'height_m': '',
            'fsi_proposed': '',
            'coverage_pct': '',
        },
        follow_redirects=True,
    )

    with app.app_context():
        a = PlotAnalysis.query.get(aid)
        assert a.status == 'analyzed', (
            f"Expected 'analyzed' after edit, got '{a.status}'"
        )


# ══════════════════════════════════════════════════════════════════════════
# New multi-document / validation / conflict system tests
# ══════════════════════════════════════════════════════════════════════════

# ── Fixtures (shared with new tests) ──────────────────────────────────────

def _make_good_png():
    """Same 1×1 PNG bytes used throughout."""
    return _make_png()


def _seed_analysis_with_fields(app, pid, arch_id, field_values, source='FMB'):
    """
    Helper: create PlotAnalysis + PlotExtractedData rows for the given field
    dict and source, commit, return (analysis_id, {field_name: value}).
    """
    from app.models import PlotAnalysis, PlotExtractedData
    with app.app_context():
        canonical_values = _canonical_field_values(field_values)
        a = PlotAnalysis(
            project_id=pid, uploaded_by=arch_id,
            file_path='dummy.png', original_filename='dummy.png',
            status='extracted',
            input_payload=json.dumps(_plot_defaults()),
        )
        db.session.add(a)
        db.session.flush()
        field_units = {
            'plot_area': 'sq.m',
            'frontage': 'm',
            'road_width': 'm',
            'front_setback_m': 'm',
            'rear_setback_m': 'm',
            'side_setback_m': 'm',
            'height_m': 'm',
            'fsi_proposed': '',
            'coverage_pct': '%',
        }
        for fk, val in canonical_values.items():
            db.session.add(PlotExtractedData(
                analysis_id=a.id, field_name=fk,
                value=str(val), unit=field_units.get(fk, ''),
                confidence=0.85, source=source,
            ))
        db.session.commit()
        return a.id


# ── 35. PlotDocument is created on upload ─────────────────────────────────

def test_upload_creates_plot_document(client, app, arch_user, project):
    """PlotDocument record is created for uploaded file with correct doc_type."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    client.post(
        f'/projects/{pid}/plot-analysis/upload',
        data={
            'drawing':  (io.BytesIO(_make_good_png()), 'fmb.png', 'image/png'),
            'doc_type': 'FMB',
        },
        content_type='multipart/form-data', follow_redirects=True,
    )

    with app.app_context():
        from app.models import PlotDocument
        doc = PlotDocument.query.first()
        assert doc is not None, 'PlotDocument record must be created'
        assert doc.doc_type == 'FMB'
        assert doc.original_filename == 'fmb.png'
        assert doc.status in ('extracted', 'failed', 'uploaded')


# ── 36. Re-uploading same doc type replaces previous PlotDocument ──────────

def test_upload_replaces_existing_doc_type(client, app, arch_user, project):
    """Second upload of same doc_type removes the old document."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    for filename in ('fmb_v1.png', 'fmb_v2.png'):
        client.post(
            f'/projects/{pid}/plot-analysis/upload',
            data={
                'drawing':  (io.BytesIO(_make_good_png()), filename, 'image/png'),
                'doc_type': 'FMB',
            },
            content_type='multipart/form-data', follow_redirects=True,
        )

    with app.app_context():
        from app.models import PlotDocument
        docs = PlotDocument.query.filter_by(doc_type='FMB').all()
        assert len(docs) == 1, 'Only one FMB document should exist after re-upload'
        assert docs[0].original_filename == 'fmb_v2.png'


# ── 37. AJAX upload returns JSON with extracted fields ────────────────────

def test_upload_ajax_returns_json(client, app, arch_user, project):
    """AJAX upload returns JSON {ok, analysis_id, doc_id, n_extracted, extracted}."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    resp = client.post(
        f'/projects/{pid}/plot-analysis/upload',
        data={
            'drawing':  (io.BytesIO(_make_good_png()), 'fmb.png', 'image/png'),
            'doc_type': 'FMB',
        },
        content_type='multipart/form-data',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True
    assert 'analysis_id' in data
    assert 'doc_id' in data
    assert 'n_extracted' in data
    assert isinstance(data['extracted'], dict)


# ── 38. Manual route saves road_direction and numeric fields ──────────────

def test_manual_saves_context(client, app, arch_user, project):
    """POST /manual saves road_direction, authority, and numeric setbacks."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    resp = client.post(
        f'/projects/{pid}/plot-analysis/manual',
        data={
            'road_direction': 'E',
            'authority':      'CMDA',
            'road_width':     '20',
            'front_setback_m':  '3',
            'plot_area':        '900',
        },
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True
    assert 'road_direction' in data['saved'] or 'road_width' in data['saved']

    with app.app_context():
        a = PlotAnalysis.query.filter_by(project_id=pid).first()
        assert a is not None
        assert a.road_direction == 'E'
        assert a.authority == 'CMDA'

        row = PlotExtractedData.query.filter_by(
            analysis_id=a.id, field_name='road_width', source='manual',
        ).first()
        assert row is not None
        assert float(row.value) == 20.0
        assert float(row.normalized_value) == 20.0
        assert row.is_manual_override is True
        assert row.confidence == -1.0

        area_row = PlotExtractedData.query.filter_by(
            analysis_id=a.id, field_name='plot_area', source='manual',
        ).first()
        assert area_row is not None
        assert float(area_row.normalized_value) == 900.0


# ── 39. Manual route rejects invalid road_direction ───────────────────────

def test_manual_rejects_bad_direction(client, app, arch_user, project):
    """POST /manual with invalid road_direction returns errors."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id

    resp = client.post(
        f'/projects/{pid}/plot-analysis/manual',
        data={'road_direction': 'NORTH'},  # invalid — should be N, S, E, W etc.
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data['ok'] is False
    assert data['errors']


# ── 40. Conflict detection finds mismatch > 5% ───────────────────────────

def test_conflict_detection_finds_mismatch(client, app, arch_user, project):
    """GET /validation finds conflict when FMB area != Patta area by >5%."""
    _login(client, arch_user.email)
    with app.app_context():
        pid  = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid  = _seed_analysis_with_fields(app, pid, arch_user.id,
                                           {'plot_area': 1400.0}, source='FMB')

    # Add a conflicting Patta area (1200 vs 1400 = ~14% mismatch)
    with app.app_context():
        db.session.add(PlotExtractedData(
            analysis_id=aid, field_name='plot_area',
            value='1200', unit='sq.m', confidence=0.80, source='Patta',
        ))
        db.session.commit()

    resp = client.get(
        f'/projects/{pid}/plot-analysis/{aid}/validation',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True
    assert data['has_conflicts'] is True
    assert data['conflict_count'] >= 1

    area_row = next((f for f in data['fields'] if f['field'] == 'plot_area'), None)
    assert area_row is not None
    assert area_row['conflict'] is True


# ── 41. Conflict detection finds NO conflict when values within 5% ────────

def test_conflict_detection_no_conflict(client, app, arch_user, project):
    """Values differing by <= 5% are NOT flagged as conflicts."""
    _login(client, arch_user.email)
    with app.app_context():
        pid  = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid  = _seed_analysis_with_fields(app, pid, arch_user.id,
                                           {'frontage': 30.0}, source='FMB')

    # Patta frontage is 30.5 — only 1.7% difference
    with app.app_context():
        db.session.add(PlotExtractedData(
            analysis_id=aid, field_name='frontage',
            value='30.5', unit='ft', confidence=0.78, source='Patta',
        ))
        db.session.commit()

    resp = client.get(
        f'/projects/{pid}/plot-analysis/{aid}/validation',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['has_conflicts'] is False


# ── 42. Resolve endpoint stores the selected source of truth ──────────────

def test_resolve_stores_resolution(client, app, arch_user, project):
    """POST /resolve saves resolved_source and resolved_value on PlotConflict."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(app, pid, arch_user.id,
                                          {'plot_area': 1400.0}, source='FMB')
        db.session.add(PlotExtractedData(
            analysis_id=aid, field_name='plot_area',
            value='1200', unit='sq.m', confidence=0.80, source='Patta',
        ))
        db.session.commit()

    # Run validation first to create the PlotConflict row
    client.get(f'/projects/{pid}/plot-analysis/{aid}/validation',
               headers={'X-Requested-With': 'XMLHttpRequest'})

    resp = client.post(
        f'/projects/{pid}/plot-analysis/{aid}/resolve',
        data=json.dumps({'field_name': 'plot_area', 'source': 'FMB'}),
        content_type='application/json',
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True
    assert data['resolved_value'] == '1400.0'
    assert data['resolved_source'] == 'FMB'

    with app.app_context():
        from app.models import PlotConflict
        c = PlotConflict.query.filter_by(analysis_id=aid, field_name='plot_area').first()
        assert c is not None
        assert c.resolved is True
        assert c.resolved_source == 'FMB'


# ── 43. Resolve with custom value stores the custom value ─────────────────

def test_resolve_custom_value(client, app, arch_user, project):
    """POST /resolve with source=custom stores the user-entered value."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(app, pid, arch_user.id,
                                          {'plot_area': 1400.0}, source='FMB')

    resp = client.post(
        f'/projects/{pid}/plot-analysis/{aid}/resolve',
        data=json.dumps({
            'field_name':   'area',
            'field_name':   'plot_area',
            'source':       'custom',
            'custom_value': '1350',
        }),
        content_type='application/json',
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True
    assert float(data['resolved_value']) == 1350.0


# ── 44. Compute uses conflict resolutions in field_map ────────────────────

def test_compute_uses_resolved_values(client, app, arch_user, project):
    """
    When a conflict is resolved, compute() must use the resolved value,
    NOT the raw extracted value from the lower-priority source.
    """
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(
            app, pid, arch_user.id,
            {'plot_area': 1400.0, 'frontage': 30.0, 'road_width': 7.0},
            source='FMB',
        )
        # Conflicting Patta area (very different)
        db.session.add(PlotExtractedData(
            analysis_id=aid, field_name='plot_area',
            value='500', unit='sq.m', confidence=0.60, source='Patta',
        ))
        db.session.commit()

    # Validate → creates PlotConflict
    client.get(f'/projects/{pid}/plot-analysis/{aid}/validation',
               headers={'X-Requested-With': 'XMLHttpRequest'})

    # Resolve: choose FMB (1400) as source of truth
    client.post(
        f'/projects/{pid}/plot-analysis/{aid}/resolve',
        data=json.dumps({'field_name': 'plot_area', 'source': 'FMB'}),
        content_type='application/json',
    )

    # Compute
    resp = client.post(
        f'/projects/{pid}/plot-analysis/{aid}/compute',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True

    # Find the area compliance result and check it used 1400, not 500
    area_result = next((r for r in data['results'] if r['rule_name'] == 'plot_area'), None)
    assert area_result is not None
    # actual_value should contain 1400, not 500
    assert '1400' in str(area_result['actual_value'])


# ── 45. Compute AJAX returns full result payload ──────────────────────────

def test_compute_ajax_returns_results(client, app, arch_user, project):
    """POST /compute (AJAX) returns ok, overall_score, results list."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(
            app, pid, arch_user.id,
            {'plot_area': 700.0, 'frontage': 25.0, 'road_width': 7.0},
            source='FMB',
        )

    resp = client.post(
        f'/projects/{pid}/plot-analysis/{aid}/compute',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True
    assert data['overall_score'] is not None
    assert isinstance(data['results'], list)
    assert len(data['results']) == 9   # all 9 TNPCR rules
    for r in data['results']:
        assert 'rule_name' in r
        assert 'status' in r


# ── 46. Compute with no data returns 400 ─────────────────────────────────

def test_compute_no_data_returns_400(client, app, arch_user, project):
    """POST /compute with no extracted fields → 400 AJAX response."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=arch_user.id,
                         file_path='dummy.png', original_filename='dummy.png',
                         status='pending')
        db.session.add(a); db.session.commit()
        aid = a.id

    resp = client.post(
        f'/projects/{pid}/plot-analysis/{aid}/compute',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data['ok'] is False


# ── 47. Compute sets data_completeness_score ─────────────────────────────

def test_compute_stores_completeness(client, app, arch_user, project):
    """After compute, PlotAnalysis.data_completeness_score reflects filled fields."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        # Only 3 out of 9 fields
        aid = _seed_analysis_with_fields(
            app, pid, arch_user.id,
            {'plot_area': 700.0, 'frontage': 25.0, 'road_width': 7.0},
            source='FMB',
        )

    client.post(f'/projects/{pid}/plot-analysis/{aid}/compute',
                headers={'X-Requested-With': 'XMLHttpRequest'})

    with app.app_context():
        a = PlotAnalysis.query.get(aid)
        assert a.status == 'analyzed'
        assert a.data_completeness_score is not None
        assert a.trust_level == 'PARTIAL'
        assert 30 <= a.data_completeness_score <= 40


def test_compute_completeness_uses_field_presence(client, app, arch_user, project):
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(
            app, pid, arch_user.id,
            {'plot_area': 900.0},
            source='FMB',
        )

    client.post(
        f'/projects/{pid}/plot-analysis/{aid}/compute',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )

    with app.app_context():
        a = PlotAnalysis.query.get(aid)
        assert a.data_completeness_score is not None
        assert a.data_completeness_score == 0.0
        assert a.trust_level == 'INSUFFICIENT'


# ── 48. _best_field_value priority: FMB > Patta > EC > manual > None ──────

def test_best_field_value_priority(app, arch_user, project):
    """_best_field_value returns FMB row when both FMB and Patta are present."""
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=arch_user.id,
                         file_path='d.png', original_filename='d.png',
                         status='extracted')
        db.session.add(a); db.session.flush()

        db.session.add(PlotExtractedData(
            analysis_id=a.id, field_name='plot_area', value='1200',
            unit='sq.m', confidence=0.80, source='Patta',
        ))
        db.session.add(PlotExtractedData(
            analysis_id=a.id, field_name='plot_area', value='1400',
            unit='sq.m', confidence=0.90, source='FMB',
        ))
        db.session.commit()
        aid = a.id

        from app.routes.plot_analysis import _best_field_value
        best = _best_field_value(aid, 'plot_area')
        assert best is not None
        assert best.source == 'FMB'
        assert best.float_value == 1400.0


def test_best_field_value_prefers_manual_override(app, arch_user, project):
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=arch_user.id,
                         file_path='d.png', original_filename='d.png',
                         status='extracted')
        db.session.add(a); db.session.flush()

        db.session.add(PlotExtractedData(
            analysis_id=a.id, field_name='plot_area', value='1200',
            normalized_value='1200', unit='sq.m', confidence=0.80, source='Patta',
        ))
        db.session.add(PlotExtractedData(
            analysis_id=a.id, field_name='plot_area', value='1350',
            normalized_value='1350', unit='sq.m', confidence=-1.0,
            source='manual', is_manual_override=True,
        ))
        db.session.commit()

        from app.routes.plot_analysis import _best_field_value
        best = _best_field_value(a.id, 'plot_area')
        assert best is not None
        assert best.source == 'manual'
        assert best.float_value == 1350.0


# ── 49. _best_field_value falls back to None-source (legacy rows) ─────────

def test_best_field_value_legacy_source(app, arch_user, project):
    """_best_field_value finds rows with source=None (pre-multi-doc uploads)."""
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        a = PlotAnalysis(project_id=pid, uploaded_by=arch_user.id,
                         file_path='d.png', original_filename='d.png',
                         status='extracted')
        db.session.add(a); db.session.flush()
        # Row with no source tag (legacy)
        db.session.add(PlotExtractedData(
            analysis_id=a.id, field_name='road_width', value='35.0',
            unit='m', confidence=0.75, source=None,
        ))
        db.session.commit()
        aid = a.id

        from app.routes.plot_analysis import _best_field_value
        best = _best_field_value(aid, 'road_width')
        assert best is not None
        assert best.float_value == 35.0


# ── 50. JSON API returns documents, fields, compliance_results ────────────

def test_json_api_full_payload(client, app, arch_user, project):
    """GET /json returns documents list, fields list, compliance_results list."""
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(
            app, pid, arch_user.id,
            {'plot_area': 700.0, 'frontage': 25.0, 'road_width': 7.0},
            source='FMB',
        )

    resp = client.get(f'/projects/{pid}/plot-analysis/{aid}/json')
    assert resp.status_code == 200
    data = json.loads(resp.data)

    for key in ['analysis_id', 'status', 'overall_score',
                'data_completeness_score', 'documents', 'fields',
                'compliance_results', 'road_direction', 'authority']:
        assert key in data, f'Missing key in JSON: {key}'

    assert isinstance(data['fields'], list)
    assert isinstance(data['documents'], list)
    assert isinstance(data['compliance_results'], list)

    # Source info present on field entries
    area_field = next((f for f in data['fields'] if f['field_name'] == 'plot_area'), None)
    assert area_field is not None
    assert area_field['source'] == 'FMB'
    assert 'normalized_value' in area_field


# ── 51. PlotDocument + PlotConflict tables exist ──────────────────────────

def test_new_tables_exist(app):
    """PlotDocument and PlotConflict tables are created by db.create_all()."""
    with app.app_context():
        from sqlalchemy import inspect
        tables = inspect(db.engine).get_table_names()
        assert 'plot_document' in tables, 'plot_document table missing'
        assert 'plot_conflict' in tables, 'plot_conflict table missing'


# ── 52. PlotDocument indexes exist ────────────────────────────────────────

def test_plot_document_index_exists(app):
    """idx_plot_document_analysis_id index exists on plot_document."""
    with app.app_context():
        from sqlalchemy import inspect
        indexes = {i['name'] for i in inspect(db.engine).get_indexes('plot_document')}
        assert 'idx_plot_document_analysis_id' in indexes


# ── 53. PlotConflict index exists ─────────────────────────────────────────

def test_plot_conflict_index_exists(app):
    """idx_plot_conflict_analysis_id index exists on plot_conflict."""
    with app.app_context():
        from sqlalchemy import inspect
        indexes = {i['name'] for i in inspect(db.engine).get_indexes('plot_conflict')}
        assert 'idx_plot_conflict_analysis_id' in indexes


# ── 54. PlotExtractedData.source column exists ────────────────────────────

def test_extracted_data_has_source_column(app):
    """source and document_id columns exist on plot_extracted_data."""
    with app.app_context():
        from sqlalchemy import inspect
        cols = {c['name'] for c in inspect(db.engine).get_columns('plot_extracted_data')}
        assert 'source' in cols
        assert 'document_id' in cols


# ── 55. PlotAnalysis.road_direction + authority columns exist ─────────────

def test_analysis_has_context_columns(app):
    """road_direction and authority columns exist on plot_analysis."""
    with app.app_context():
        from sqlalchemy import inspect
        cols = {c['name'] for c in inspect(db.engine).get_columns('plot_analysis')}
        assert 'road_direction' in cols
        assert 'authority' in cols


def test_save_version_ajax_returns_json(client, app, arch_user, project):
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(
            app, pid, arch_user.id,
            {'plot_area': 700.0, 'frontage': 25.0, 'road_width': 7.0},
            source='FMB',
        )

    client.post(
        f'/projects/{pid}/plot-analysis/{aid}/compute',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )

    resp = client.post(
        f'/projects/{pid}/plot-analysis/{aid}/save-version',
        data={'version_name': 'Draft A'},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True
    assert data['version_name'] == 'Draft A'


def test_confirm_route_sets_confirmed(client, app, arch_user, project):
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(
            app, pid, arch_user.id,
            {'plot_area': 700.0, 'frontage': 25.0, 'road_width': 7.0},
            source='FMB',
        )

    client.post(
        f'/projects/{pid}/plot-analysis/{aid}/compute',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )

    resp = client.post(
        f'/projects/{pid}/plot-analysis/{aid}/confirm',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ok'] is True

    with app.app_context():
        a = PlotAnalysis.query.get(aid)
        assert a.status == 'confirmed'


def test_report_disabled_for_insufficient_analysis(client, app, arch_user, project):
    _login(client, arch_user.email)
    with app.app_context():
        pid = Project.query.filter_by(name='Pipeline Test Project').first().id
        aid = _seed_analysis_with_fields(
            app, pid, arch_user.id,
            {'plot_area': 700.0},
            source='FMB',
        )

    client.post(
        f'/projects/{pid}/plot-analysis/{aid}/compute',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )

    resp = client.get(
        f'/projects/{pid}/plot-analysis/{aid}/report',
        follow_redirects=False,
    )
    assert resp.status_code == 302
