import os
import tempfile

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import Project, Requirement, User
from app.rag import RAGManager


@pytest.fixture
def app():
    db_fd, db_path = tempfile.mkstemp()
    application = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + db_path,
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'assistant-regression-test',
        'UPLOAD_FOLDER': tempfile.mkdtemp(),
    })

    with application.app_context():
        db.create_all()
        architect = User(
            name='Assistant Architect',
            email='assistant-arch@test.com',
            role='architect',
            password_hash=generate_password_hash('pass123'),
        )
        client_user = User(
            name='Assistant Client',
            email='assistant-client@test.com',
            role='client',
            profession='Doctor',
            password_hash=generate_password_hash('pass123'),
        )
        db.session.add_all([architect, client_user])
        db.session.commit()

        project = Project(
            name='Assistant QA Project',
            architect_id=architect.id,
            client_id=client_user.id,
            plot_zone='Residential',
            status='Design',
            total_budget=12000000,
        )
        db.session.add(project)
        db.session.commit()

        db.session.add_all([
            Requirement(
                project_id=project.id,
                title='Kitchen cabinet',
                description='New version',
                category='Interior',
                status='done',
                source='architect',
                raised_by=architect.id,
                created_by=architect.id,
            ),
            Requirement(
                project_id=project.id,
                title='Arch new',
                description='new',
                category='General',
                status='in_progress',
                source='architect',
                raised_by=architect.id,
                created_by=architect.id,
            ),
        ])
        db.session.commit()
        yield application

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


def login_architect(client):
    client.get('/logout', follow_redirects=True)
    return client.post(
        '/login',
        data={'email': 'assistant-arch@test.com', 'password': 'pass123'},
        follow_redirects=True,
    )


@pytest.fixture
def fresh_rag_manager():
    original = RAGManager._instance
    RAGManager._instance = None
    manager = RAGManager.get_instance()
    manager.vector_store = None
    manager.handbook_chunks = []
    manager._gemini_disabled = True
    yield manager
    RAGManager._instance = original


def test_assistant_workspace_moves_support_panels_below_chat(client, app):
    login_architect(client)

    with app.app_context():
        project = Project.query.filter_by(name='Assistant QA Project').first()

    response = client.get(f'/projects/{project.id}/assistant')
    assert response.status_code == 200
    assert b'assistant-support-grid' in response.data
    assert b'assistant-layout' not in response.data
    assert b'id="rag-form"' not in response.data
    assert b'Project Context' in response.data
    assert b'Recent Requirement Activity' in response.data


def test_broad_architect_prompt_uses_structured_advisory_fallback(fresh_rag_manager, monkeypatch):
    fresh_rag_manager.handbook_chunks = [
        'Application for planning permission requires a submission from the owner or authorized holder.',
    ]
    monkeypatch.setattr(fresh_rag_manager, '_query_with_gemini', lambda *args, **kwargs: None)

    answer = fresh_rag_manager.query(
        'What approval risks should I flag on this project right now?',
        audience='architect',
        project_context={
            'project_name': 'Sharma Residence',
            'project_status': 'Design',
            'plot_zone': 'Residential',
        },
    )

    assert 'Approval Risk Review' in answer
    assert 'plot area' in answer.lower()
    assert 'Handbook-grounded notes:' not in answer


def test_specific_setback_prompt_keeps_handbook_grounded_notes(fresh_rag_manager, monkeypatch):
    fresh_rag_manager.handbook_chunks = [
        'In the event of prescribed minimum setback being 1.5m the clear open space shall be maintained.',
        'Rear setback requirements should be validated with the applicable occupancy controls.',
    ]
    monkeypatch.setattr(fresh_rag_manager, '_query_with_gemini', lambda *args, **kwargs: None)

    answer = fresh_rag_manager.query(
        'What is the setback?',
        audience='architect',
        project_context={'project_name': 'Sharma Residence'},
    )

    assert 'Setback Control' in answer
    assert 'Handbook-grounded notes:' in answer
    assert 'minimum setback' in answer.lower()


def test_gemini_grounded_answer_takes_priority_when_available(fresh_rag_manager, monkeypatch):
    fresh_rag_manager.handbook_chunks = [
        'In the event of prescribed minimum setback being 1.5m the clear open space shall be maintained.',
    ]
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(fresh_rag_manager, '_query_with_anthropic', lambda *args, **kwargs: 'Claude grounded answer')
    monkeypatch.setattr(fresh_rag_manager, '_query_with_gemini', lambda *args, **kwargs: 'Gemini grounded answer')

    answer = fresh_rag_manager.query(
        'What is the setback?',
        audience='architect',
        project_context={'project_name': 'Sharma Residence'},
    )

    assert answer == 'Gemini grounded answer'
