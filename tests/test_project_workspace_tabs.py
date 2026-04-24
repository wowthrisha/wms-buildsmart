import os
import tempfile

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import Project, User


@pytest.fixture
def app():
    db_fd, db_path = tempfile.mkstemp()
    application = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + db_path,
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'workspace-tabs-test',
        'UPLOAD_FOLDER': tempfile.mkdtemp(),
    })
    with application.app_context():
        db.create_all()
        architect = User(
            name='Workspace Architect',
            email='workspace-arch@test.com',
            role='architect',
            password_hash=generate_password_hash('pass123'),
        )
        client = User(
            name='Workspace Client',
            email='workspace-client@test.com',
            role='client',
            password_hash=generate_password_hash('pass123'),
        )
        db.session.add_all([architect, client])
        db.session.commit()

        project = Project(
            name='Workspace QA Project',
            architect_id=architect.id,
            client_id=client.id,
            plot_zone='Commercial',
            status='Design',
            total_budget=12000000,
        )
        db.session.add(project)
        db.session.commit()
        yield application

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


def login(client):
    client.get('/logout', follow_redirects=True)
    return client.post(
        '/login',
        data={'email': 'workspace-arch@test.com', 'password': 'pass123'},
        follow_redirects=True,
    )


def test_project_tabs_render_for_architect(client, app):
    login(client)

    with app.app_context():
        project = Project.query.filter_by(name='Workspace QA Project').first()
        project_id = project.id

    tab_expectations = [
        (f'/projects/{project_id}', b'Project Workspace'),
        (f'/projects/{project_id}/documents', b'Project Documents'),
        (f'/projects/{project_id}/compliance', b'Project Compliance'),
        (f'/projects/{project_id}/plot-analysis', b'Plot Analysis'),
        (f'/projects/{project_id}/references', b'Visual References'),
        (f'/projects/{project_id}/meetings', b'Meetings'),
        (f'/projects/{project_id}/requirements', b'Requirements'),
        (f'/projects/{project_id}/assistant', b'Ask BuildSmart'),
        (f'/projects/{project_id}/payments', b'Payments Ledger'),
        (f'/projects/{project_id}/activity', b'Project Activity'),
    ]

    for url, marker in tab_expectations:
        response = client.get(url)
        assert response.status_code == 200, url
        assert b'Workspace QA Project' in response.data, url
        assert marker in response.data, url
