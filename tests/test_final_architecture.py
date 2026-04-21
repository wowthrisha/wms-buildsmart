import io
import json
import os

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.compliance_service import ensure_project_compliance_items
from app.models import ComplianceItem, Document, PlotAnalysis, Project, User


@pytest.fixture
def app(tmp_path):
    upload_folder = str(tmp_path / 'uploads')
    os.makedirs(upload_folder, exist_ok=True)
    application = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'final-architecture-test',
        'UPLOAD_FOLDER': upload_folder,
    })
    with application.app_context():
        db.create_all()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed(app):
    with app.app_context():
        architect = User(
            name='Architect',
            email='arch@test.com',
            role='architect',
            password_hash=generate_password_hash('pass123'),
        )
        client_user = User(
            name='Client',
            email='client@test.com',
            role='client',
            password_hash=generate_password_hash('pass123'),
        )
        db.session.add_all([architect, client_user])
        db.session.commit()

        project = Project(
            name='Architecture Flow Project',
            architect_id=architect.id,
            client_id=client_user.id,
            status='Design',
            plot_zone='Mixed',
        )
        db.session.add(project)
        db.session.commit()
        return {
            'project_id': project.id,
            'architect_email': architect.email,
            'client_email': client_user.email,
        }


def _login(test_client, email, password='pass123'):
    return test_client.post('/login', data={'email': email, 'password': password}, follow_redirects=True)


def _pdf_bytes():
    return b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF'


def test_compliance_upload_goes_through_vault_and_links_document(client, app, seed):
    _login(client, seed['architect_email'])

    with app.app_context():
        ensure_project_compliance_items(seed['project_id'])
        db.session.commit()
        item = ComplianceItem.query.filter_by(project_id=seed['project_id'], doc_type='patta').first()
        assert item is not None

    upload = client.post(
        f"/projects/{seed['project_id']}/compliance/{item.id}/upload",
        data={
            'file': (io.BytesIO(_pdf_bytes()), 'patta.pdf'),
        },
        content_type='multipart/form-data',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert upload.status_code == 200
    payload = upload.get_json()
    assert payload['success'] is True
    assert 'document_id' in payload

    with app.app_context():
        stored = db.session.get(Document, payload['document_id'])
        linked_item = db.session.get(ComplianceItem, item.id)
        assert stored is not None
        assert stored.source_module == 'compliance'
        assert stored.file_path
        assert linked_item.document_id == stored.id
        assert linked_item.status == 'uploaded'
        assert not hasattr(linked_item, 'file_path')


def test_client_compliance_upload_stays_pending_until_architect_verifies(client, app, seed):
    _login(client, seed['client_email'])

    with app.app_context():
        ensure_project_compliance_items(seed['project_id'])
        db.session.commit()
        item = ComplianceItem.query.filter_by(project_id=seed['project_id'], doc_type='ec').first()
        assert item is not None

    upload = client.post(
        f"/projects/{seed['project_id']}/compliance/{item.id}/upload",
        data={
            'file': (io.BytesIO(_pdf_bytes()), 'ec.pdf'),
        },
        content_type='multipart/form-data',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert upload.status_code == 200
    payload = upload.get_json()
    assert payload['success'] is True

    with app.app_context():
        stored = db.session.get(Document, payload['document_id'])
        linked_item = db.session.get(ComplianceItem, item.id)
        assert stored is not None
        assert stored.source_module == 'compliance'
        assert stored.pending_verification is True
        assert stored.uploader_role == 'client'
        assert linked_item.status == 'client_uploaded'
        assert linked_item.arch_checked is False


def test_architect_verifies_client_compliance_upload_and_promotes_status(client, app, seed):
    _login(client, seed['client_email'])

    with app.app_context():
        ensure_project_compliance_items(seed['project_id'])
        db.session.commit()
        item = ComplianceItem.query.filter_by(project_id=seed['project_id'], doc_type='sale_deed').first()
        assert item is not None

    upload = client.post(
        f"/projects/{seed['project_id']}/compliance/{item.id}/upload",
        data={
            'file': (io.BytesIO(_pdf_bytes()), 'sale-deed.pdf'),
        },
        content_type='multipart/form-data',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert upload.status_code == 200

    client.get('/logout', follow_redirects=True)
    _login(client, seed['architect_email'])
    verify = client.post(
        f"/projects/{seed['project_id']}/compliance/verify/{item.id}",
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert verify.status_code == 200
    payload = verify.get_json()
    assert payload['success'] is True

    with app.app_context():
        linked_item = db.session.get(ComplianceItem, item.id)
        stored = db.session.get(Document, linked_item.document_id)
        assert linked_item.status == 'verified'
        assert linked_item.arch_checked is True
        assert stored.pending_verification is False
        assert stored.verified_by is not None


def test_client_plot_workspace_renders_compliance_tab(client, app, seed):
    _login(client, seed['client_email'])

    response = client.get(f"/projects/{seed['project_id']}/plot-analysis/client-view", follow_redirects=True)
    assert response.status_code == 200
    assert b'Plot Intelligence Workspace' in response.data
    assert b'Compliance Checklist' in response.data
    assert b'Upload supporting documents' in response.data


def test_plot_analysis_runs_without_document_dependency(client, app, seed):
    _login(client, seed['architect_email'])

    resp = client.post(
        f"/projects/{seed['project_id']}/plot-analysis/compute",
        data={
            'plot_area': '500',
            'road_width': '7',
            'frontage': '10',
            'building_type': 'residential',
            'location_type': 'corporation',
            'height_m': '8',
            'fsi_proposed': '1.8',
            'coverage_pct': '60',
            'front_setback_m': '3',
            'side_setback_m': '1.5',
            'rear_setback_m': '1.5',
        },
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['ok'] is True
    assert payload['result']['valid'] is True
    assert 'applied_rules' in payload['result']

    with app.app_context():
        analysis = PlotAnalysis.query.filter_by(project_id=seed['project_id']).first()
        assert analysis is not None
        stored_result = json.loads(analysis.result_payload)
        assert stored_result['valid'] is True
        assert stored_result['applied_rules']


def test_client_settings_persist_profession_and_notification_preferences(client, app, seed):
    _login(client, seed['client_email'])

    response = client.post(
        '/settings',
        data={
            'name': 'Client',
            'phone': '+91 99999 00000',
            'profession': 'Doctor',
            'email': 'on',
            'sms': 'on',
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        client_user = User.query.filter_by(email=seed['client_email']).first()
        assert client_user.profession == 'Doctor'
        assert client_user.reminders_email is True
        assert client_user.reminders_sms is True


def test_client_assistant_query_returns_profession_aware_payload(client, app, seed, monkeypatch):
    with app.app_context():
        client_user = User.query.filter_by(email=seed['client_email']).first()
        client_user.profession = 'Doctor'
        db.session.commit()

    _login(client, seed['client_email'])
    captured = {}

    def fake_answer(question, **kwargs):
        captured['question'] = question
        captured.update(kwargs)
        return 'Profession-aware answer'

    monkeypatch.setattr('app.rag.answer_question', fake_answer)

    response = client.post(
        '/my_project/assistant/query',
        json={'question': 'Explain FAR for me'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['answer'] == 'Profession-aware answer'
    assert payload['audience'] == 'client'
    assert payload['profession'] == 'Doctor'
    assert captured['question'] == 'Explain FAR for me'
    assert captured['audience'] == 'client'
    assert captured['profession'] == 'Doctor'
    assert captured['project_context']['project_name'] == 'Architecture Flow Project'
