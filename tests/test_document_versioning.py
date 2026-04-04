"""
Tests for Document Versioning + Access Control system.

Covers:
- Upload creates version 1 for new document
- Upload same title again creates version 2
- Architect hides doc -> client cannot see or download it
- Client upload is hidden from client portal by default
- toggle-visibility flips visible_to_client
- GET /documents/project/<id> returns filtered list per role
- Edge cases: same name different project, missing file
"""
import io
import pytest
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.models import User, Project, Document, DocumentVersion


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    import tempfile, os
    db_fd, db_path = tempfile.mkstemp()
    application = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + db_path,
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test-secret',
        'UPLOAD_FOLDER': tempfile.mkdtemp(),
    })
    with application.app_context():
        db.create_all()
        yield application
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_users_and_project(app):
    with app.app_context():
        arch = User(
            name='Arch User', email='arch@test.com', role='architect',
            password_hash=generate_password_hash('pass123'),
        )
        cli = User(
            name='Client User', email='client@test.com', role='client',
            password_hash=generate_password_hash('pass123'),
        )
        db.session.add_all([arch, cli])
        db.session.commit()

        proj = Project(name='Test Project', architect_id=arch.id, client_id=cli.id)
        db.session.add(proj)
        db.session.commit()
        return {'arch_id': arch.id, 'client_id': cli.id, 'project_id': proj.id}


# ── Helpers ─────────────────────────────────────────────────────────────────

def login(client, email, password):
    resp = client.get('/login')
    # CSRF disabled in tests — post directly
    return client.post('/login', data={'email': email, 'password': password}, follow_redirects=True)


def upload_doc(client, project_id, filename='plan.pdf', title=None, content=b'%PDF-1.4 test'):
    data = {
        'doc': (io.BytesIO(content), filename),
        'category': 'Site Plan',
    }
    if title:
        data['title'] = title
    return client.post(
        f'/projects/{project_id}/documents/upload',
        data=data,
        content_type='multipart/form-data',
        follow_redirects=True,
    )


# ── Tests ────────────────────────────────────────────────────────────────────

class TestUploadVersioning:
    def test_upload_new_document_creates_version_1(self, client, app, seed_users_and_project):
        """Uploading a new title creates Document + DocumentVersion with version_num=1."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']

        upload_doc(client, pid, filename='floor_plan.pdf', title='Floor Plan')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Floor Plan', project_id=pid).first()
            assert doc is not None, 'Document was not created'
            assert doc.versions.count() == 1
            v = doc.versions.first()
            assert v.version_num == 1

    def test_upload_same_title_creates_version_2(self, client, app, seed_users_and_project):
        """Uploading the same title again increments version to 2."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']

        upload_doc(client, pid, filename='elevation_v1.pdf', title='Elevation')
        upload_doc(client, pid, filename='elevation_v2.pdf', title='Elevation')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Elevation', project_id=pid).first()
            assert doc.versions.count() == 2
            versions = doc.versions.order_by(DocumentVersion.version_num).all()
            assert versions[0].version_num == 1
            assert versions[1].version_num == 2

    def test_same_title_different_project_creates_separate_documents(self, client, app, seed_users_and_project):
        """Same title in different projects creates independent Document records."""
        login(client, 'arch@test.com', 'pass123')
        pid1 = seed_users_and_project['project_id']

        with app.app_context():
            arch = User.query.filter_by(email='arch@test.com').first()
            cli = User.query.filter_by(email='client@test.com').first()
            proj2 = Project(name='Project 2', architect_id=arch.id, client_id=cli.id)
            db.session.add(proj2)
            db.session.commit()
            pid2 = proj2.id

        upload_doc(client, pid1, filename='plan.pdf', title='Site Plan')
        upload_doc(client, pid2, filename='plan.pdf', title='Site Plan')

        with app.app_context():
            docs = Document.query.filter_by(original_name='Site Plan').all()
            assert len(docs) == 2
            assert {d.project_id for d in docs} == {pid1, pid2}

    def test_version_stores_file_type_and_size(self, client, app, seed_users_and_project):
        """DocumentVersion records file_type and file_size."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']

        upload_doc(client, pid, filename='spec.pdf', title='Spec Sheet', content=b'%PDF-1.4 ' + b'x' * 2048)

        with app.app_context():
            doc = Document.query.filter_by(original_name='Spec Sheet').first()
            v = doc.versions.first()
            assert v.file_type is not None
            assert v.file_size is not None


class TestAccessControl:
    def test_new_document_hidden_from_client_by_default(self, client, app, seed_users_and_project):
        """Architect upload defaults visible_to_client=False; client cannot see it."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='hidden.pdf', title='Hidden Doc')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Hidden Doc').first()
            assert doc.visible_to_client is False

        # Client logs in and checks portal
        client.get('/logout', follow_redirects=True)
        login(client, 'client@test.com', 'pass123')
        resp = client.get('/my_project/documents', follow_redirects=True)
        assert b'Hidden Doc' not in resp.data

    def test_client_cannot_download_hidden_document(self, client, app, seed_users_and_project):
        """Client gets 403 when downloading a doc with visible_to_client=False."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='secret.pdf', title='Secret')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Secret').first()
            doc_id = doc.id

        client.get('/logout', follow_redirects=True)
        login(client, 'client@test.com', 'pass123')
        resp = client.get(f'/documents/{doc_id}/download')
        assert resp.status_code == 403

    def test_toggle_visibility_shows_document_to_client(self, client, app, seed_users_and_project):
        """After toggle, visible_to_client flips to True and client can see the doc."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='shared.pdf', title='Shared Doc')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Shared Doc').first()
            doc_id = doc.id

        resp = client.post(f'/documents/{doc_id}/toggle-visibility')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['visible_to_client'] is True

        with app.app_context():
            doc = db.session.get(Document, doc_id)
            assert doc.visible_to_client is True

        # Client can now see it
        client.get('/logout', follow_redirects=True)
        login(client, 'client@test.com', 'pass123')
        resp = client.get('/my_project/documents', follow_redirects=True)
        assert b'Shared Doc' in resp.data

    def test_toggle_visibility_twice_hides_again(self, client, app, seed_users_and_project):
        """Double-toggle returns visible_to_client to False."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='back.pdf', title='Toggle Twice')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Toggle Twice').first()
            doc_id = doc.id

        client.post(f'/documents/{doc_id}/toggle-visibility')
        resp = client.post(f'/documents/{doc_id}/toggle-visibility')
        data = resp.get_json()
        assert data['visible_to_client'] is False

    def test_client_upload_hidden_by_default(self, client, app, seed_users_and_project):
        """Client-uploaded documents have visible_to_client=False by default."""
        login(client, 'client@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='client_doc.pdf', title='Client Upload')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Client Upload').first()
            assert doc is not None
            assert doc.visible_to_client is False
            assert doc.uploader_role == 'client'

    def test_architect_sees_all_documents_via_api(self, client, app, seed_users_and_project):
        """GET /documents/project/<id> returns all docs for architect regardless of visibility."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']

        upload_doc(client, pid, filename='a.pdf', title='Doc A')
        upload_doc(client, pid, filename='b.pdf', title='Doc B')

        # Toggle only Doc A visible
        with app.app_context():
            doc_a = Document.query.filter_by(original_name='Doc A').first()
        client.post(f'/documents/{doc_a.id}/toggle-visibility')

        resp = client.get(f'/documents/project/{pid}')
        assert resp.status_code == 200
        result = resp.get_json()
        titles = {d['title'] for d in result}
        assert 'Doc A' in titles
        assert 'Doc B' in titles

    def test_client_api_returns_only_visible_documents(self, client, app, seed_users_and_project):
        """GET /documents/project/<id> filters invisible docs for client."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']

        upload_doc(client, pid, filename='visible.pdf', title='Visible')
        upload_doc(client, pid, filename='hidden.pdf', title='Hidden')

        with app.app_context():
            visible_doc = Document.query.filter_by(original_name='Visible').first()
        client.post(f'/documents/{visible_doc.id}/toggle-visibility')

        client.get('/logout', follow_redirects=True)
        login(client, 'client@test.com', 'pass123')

        resp = client.get(f'/documents/project/{pid}')
        assert resp.status_code == 200
        result = resp.get_json()
        titles = [d['title'] for d in result]
        assert 'Visible' in titles
        assert 'Hidden' not in titles

    def test_api_response_includes_version_list(self, client, app, seed_users_and_project):
        """API response versions list includes version_number, file_type, file_size, uploaded_by."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']

        upload_doc(client, pid, filename='versioned.pdf', title='Versioned')
        upload_doc(client, pid, filename='versioned_v2.pdf', title='Versioned')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Versioned').first()
        client.post(f'/documents/{doc.id}/toggle-visibility')

        resp = client.get(f'/documents/project/{pid}')
        result = resp.get_json()
        versioned = next(d for d in result if d['title'] == 'Versioned')
        assert len(versioned['versions']) == 2
        v1 = versioned['versions'][0]
        assert v1['version_number'] == 1
        assert 'file_type' in v1
        assert 'file_size' in v1
        assert 'uploaded_by' in v1
        assert 'uploaded_at' in v1


class TestEdgeCases:
    def test_upload_without_file_returns_error(self, client, app, seed_users_and_project):
        """POST upload with no file attached flashes error and redirects."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        resp = client.post(
            f'/projects/{pid}/documents/upload',
            data={'category': 'Site Plan'},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        # Should redirect (not 500) and show an error flash
        assert resp.status_code == 200

        with app.app_context():
            assert Document.query.filter_by(project_id=pid).count() == 0

    def test_toggle_visibility_by_non_owner_returns_403(self, client, app, seed_users_and_project):
        """Architect of a different project cannot toggle another architect's document."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='owned.pdf', title='Owned Doc')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Owned Doc').first()
            doc_id = doc.id
            # Create a second architect
            other = User(
                name='Other Arch', email='other@test.com', role='architect',
                password_hash=generate_password_hash('pass123'),
            )
            db.session.add(other)
            db.session.commit()

        client.get('/logout', follow_redirects=True)
        login(client, 'other@test.com', 'pass123')
        resp = client.post(f'/documents/{doc_id}/toggle-visibility')
        assert resp.status_code == 403


class TestUpdateFlow:
    """Tests for the Update (add new version) flow via upload_version route."""

    def _upload_version(self, client, doc_id, filename='v2.pdf', version_label='Rev B', content=b'%PDF-1.4 v2'):
        data = {
            'doc': (io.BytesIO(content), filename),
            'version_label': version_label,
        }
        return client.post(
            f'/documents/{doc_id}/upload-version',
            data=data,
            content_type='multipart/form-data',
            follow_redirects=True,
        )

    def test_update_creates_rev_b(self, client, app, seed_users_and_project):
        """Clicking Update and submitting creates Rev B on the same document."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='v1.pdf', title='Elevation')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Elevation').first()
            doc_id = doc.id

        self._upload_version(client, doc_id, filename='v2.pdf', version_label='Rev B')

        with app.app_context():
            doc = db.session.get(Document, doc_id)
            assert doc.versions.count() == 2
            versions = doc.all_versions
            assert versions[0].label == 'Rev A'   # auto-label since no version_label on v1
            assert versions[1].label == 'Rev B'   # explicit label
            assert versions[1].version_num == 2

    def test_update_keeps_same_document_id(self, client, app, seed_users_and_project):
        """New version is linked to the same Document, not a new one."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='plan.pdf', title='Site Plan')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Site Plan').first()
            original_doc_id = doc.id

        self._upload_version(client, original_doc_id, filename='plan2.pdf', version_label='Rev B')

        with app.app_context():
            assert Document.query.filter_by(original_name='Site Plan').count() == 1
            doc = db.session.get(Document, original_doc_id)
            assert doc.versions.count() == 2

    def test_update_stores_version_label(self, client, app, seed_users_and_project):
        """version_label from the form is stored on the new DocumentVersion."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='draft.pdf', title='Draft Plan')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Draft Plan').first()
            doc_id = doc.id

        self._upload_version(client, doc_id, filename='final.pdf', version_label='Final')

        with app.app_context():
            doc = db.session.get(Document, doc_id)
            latest = doc.current_version
            assert latest.version_label == 'Final'
            assert latest.label == 'Final'

    def test_update_filename_pointer_updated(self, client, app, seed_users_and_project):
        """Document.filename pointer moves to the latest uploaded file."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='old_file.pdf', title='Spec')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Spec').first()
            doc_id = doc.id
            old_filename = doc.filename

        self._upload_version(client, doc_id, filename='new_file.pdf', version_label='Rev B')

        with app.app_context():
            doc = db.session.get(Document, doc_id)
            assert doc.filename != old_filename

    def test_update_without_file_returns_error(self, client, app, seed_users_and_project):
        """Submitting the update form with no file does not create a new version."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='base.pdf', title='Base Doc')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Base Doc').first()
            doc_id = doc.id

        resp = client.post(
            f'/documents/{doc_id}/upload-version',
            data={'version_label': 'Rev B'},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            assert db.session.get(Document, doc_id).versions.count() == 1

    def test_version_download_route(self, client, app, seed_users_and_project):
        """GET /documents/version/<id>/download returns 200 for architect."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='dl.pdf', title='DL Doc')

        with app.app_context():
            from app.models import DocumentVersion
            v = DocumentVersion.query.first()
            version_id = v.id

        resp = client.get(f'/documents/version/{version_id}/download')
        # Should be 200 (file served) or 302 (S3 redirect) — not 403/404
        assert resp.status_code in (200, 302)

    def test_all_versions_property_ordered(self, client, app, seed_users_and_project):
        """Document.all_versions returns versions in ascending version_num order."""
        login(client, 'arch@test.com', 'pass123')
        pid = seed_users_and_project['project_id']
        upload_doc(client, pid, filename='r1.pdf', title='Rev Order Test')

        with app.app_context():
            doc = Document.query.filter_by(original_name='Rev Order Test').first()
            doc_id = doc.id

        self._upload_version(client, doc_id, filename='r2.pdf', version_label='Rev B')
        self._upload_version(client, doc_id, filename='r3.pdf', version_label='Rev C')

        with app.app_context():
            doc = db.session.get(Document, doc_id)
            versions = doc.all_versions
            nums = [v.version_num for v in versions]
            assert nums == sorted(nums)
            assert len(nums) == 3
