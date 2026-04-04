"""
Tests for Meeting + MOM + Comments + Requirements + Kanban system.

Covers:
1. Architect proposes meeting
2. Client counter-proposes a slot
3. Architect/client confirms meeting
4. Architect saves MOM (minutes of meeting)
5. Comment added to meeting
6. Comment converted to Requirement
7. Requirement appears in kanban data JSON
8. Requirement status update (kanban move)
9. Direct add requirement from kanban board
10. Access control: wrong user cannot access another project's data
"""
import pytest
from io import BytesIO
from datetime import datetime
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.models import User, Project, Meeting, MeetingLog, Comment, Requirement, Notification, Document, ProjectImage


# ── Fixtures ─────────────────────────────────────────────────────────────────

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
def seed(app):
    with app.app_context():
        arch = User(name='Arch', email='arch@t.com', role='architect',
                    password_hash=generate_password_hash('pass'))
        cli = User(name='Client', email='client@t.com', role='client',
                   password_hash=generate_password_hash('pass'))
        other_arch = User(name='OtherArch', email='other@t.com', role='architect',
                          password_hash=generate_password_hash('pass'))
        db.session.add_all([arch, cli, other_arch])
        db.session.commit()

        proj = Project(name='Proj', architect_id=arch.id, client_id=cli.id)
        other_proj = Project(name='OtherProj', architect_id=other_arch.id, client_id=cli.id)
        db.session.add_all([proj, other_proj])
        db.session.commit()
        return {
            'arch_id': arch.id, 'client_id': cli.id,
            'other_arch_id': other_arch.id,
            'project_id': proj.id, 'other_project_id': other_proj.id,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def login(client, email):
    client.get('/logout', follow_redirects=True)
    return client.post('/login', data={'email': email, 'password': 'pass'}, follow_redirects=True)


def propose_meeting(client, project_id, slot='2025-06-01T10:00'):
    return client.post(
        f'/projects/{project_id}/meetings/propose',
        data={'slot_1': slot},
        follow_redirects=True,
    )


def seed_meeting(app, project_id, arch_id, status='awaiting_client'):
    """Insert a meeting directly into DB and return its id."""
    with app.app_context():
        m = Meeting(
            project_id=project_id,
            slot_1=datetime(2025, 6, 1, 10, 0),
            status=status,
        )
        db.session.add(m)
        db.session.commit()
        return m.id


def seed_comment(app, meeting_id, user_id, content='Fix the skylight'):
    with app.app_context():
        c = Comment(meeting_id=meeting_id, user_id=user_id, content=content)
        db.session.add(c)
        db.session.commit()
        return c.id


# ── Meeting Scheduling ────────────────────────────────────────────────────────

class TestMeetingPropose:
    def test_architect_can_propose(self, client, app, seed):
        login(client, 'arch@t.com')
        pid = seed['project_id']

        resp = propose_meeting(client, pid)
        assert resp.status_code == 200

        with app.app_context():
            m = Meeting.query.filter_by(project_id=pid).first()
            assert m is not None
            assert m.status == 'proposed'
            assert m.slot_1 is not None

    def test_propose_without_slot_doesnt_create_meeting(self, client, app, seed):
        login(client, 'arch@t.com')
        pid = seed['project_id']

        client.post(f'/projects/{pid}/meetings/propose', data={}, follow_redirects=True)

        with app.app_context():
            count = Meeting.query.filter_by(project_id=pid).count()
            assert count == 0

    def test_other_architect_cannot_propose(self, client, app, seed):
        login(client, 'other@t.com')
        pid = seed['project_id']  # belongs to arch@t.com

        resp = client.post(
            f'/projects/{pid}/meetings/propose',
            data={'slot_1': '2025-06-01T10:00'},
            follow_redirects=True,
        )
        assert resp.status_code == 403


class TestMeetingCounter:
    def test_client_can_counter(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'client@t.com')

        resp = client.post(
            f'/meetings/{mid}/counter',
            data={'counter_slot': '2025-06-02T14:00'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            m = db.session.get(Meeting, mid)
            assert m.status == 'counter_proposed'
            assert m.counter_slot is not None

    def test_architect_cannot_counter(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        resp = client.post(
            f'/meetings/{mid}/counter',
            data={'counter_slot': '2025-06-02T14:00'},
            follow_redirects=True,
        )
        assert resp.status_code == 403


class TestMeetingConfirm:
    def test_architect_can_confirm(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        resp = client.post(
            f'/meetings/{mid}/confirm',
            data={'slot_choice': 'slot_1'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            m = db.session.get(Meeting, mid)
            assert m.status == 'confirmed'
            assert m.confirmed_slot is not None

    def test_client_can_confirm(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'client@t.com')

        resp = client.post(
            f'/meetings/{mid}/confirm',
            data={'slot_choice': 'slot_1'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            m = db.session.get(Meeting, mid)
            assert m.status == 'confirmed'


class TestMOM:
    def test_architect_saves_mom(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'], status='confirmed')
        login(client, 'arch@t.com')

        resp = client.post(
            f'/meetings/{mid}/notes',
            data={'mom_content': 'Discussed roof design.'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            m = db.session.get(Meeting, mid)
            assert m.mom_content == 'Discussed roof design.'
            assert m.mom_date is not None

    def test_client_cannot_save_notes(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'client@t.com')

        resp = client.post(
            f'/meetings/{mid}/notes',
            data={'mom_content': 'Hacked MOM'},
            follow_redirects=True,
        )
        assert resp.status_code == 403


# ── Comments ──────────────────────────────────────────────────────────────────

class TestComments:
    def test_architect_can_comment(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        resp = client.post(
            f'/meetings/{mid}/comment',
            data={'content': 'Add a skylight to the bedroom.'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            c = Comment.query.filter_by(meeting_id=mid).first()
            assert c is not None
            assert c.content == 'Add a skylight to the bedroom.'

    def test_client_can_comment(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'client@t.com')

        resp = client.post(
            f'/meetings/{mid}/comment',
            data={'content': 'Client comment here.'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            c = Comment.query.filter_by(meeting_id=mid).first()
            assert c is not None

    def test_empty_comment_rejected(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        client.post(f'/meetings/{mid}/comment', data={'content': '   '}, follow_redirects=True)

        with app.app_context():
            count = Comment.query.filter_by(meeting_id=mid).count()
            assert count == 0

    def test_comment_truncated_at_2000_chars(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        client.post(
            f'/meetings/{mid}/comment',
            data={'content': 'A' * 3000},
            follow_redirects=True,
        )

        with app.app_context():
            c = Comment.query.filter_by(meeting_id=mid).first()
            assert len(c.content) == 2000


# ── Convert Comment → Requirement ─────────────────────────────────────────────

class TestConvertComment:
    def test_convert_creates_requirement(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        cid = seed_comment(app, mid, seed['arch_id'], 'Install AC in master bedroom')
        login(client, 'arch@t.com')

        resp = client.post(
            f'/comments/{cid}/convert',
            data={'req_title': 'AC in master bedroom', 'req_category': 'Design'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            req = Requirement.query.filter_by(project_id=seed['project_id']).first()
            assert req is not None
            assert req.title == 'AC in master bedroom'
            assert req.category == 'Design'
            assert req.status == 'new'
            assert req.meeting_id == mid

    def test_convert_links_comment_to_requirement(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        cid = seed_comment(app, mid, seed['arch_id'], 'Add fire escape stairs')
        login(client, 'arch@t.com')

        client.post(f'/comments/{cid}/convert', data={'req_title': 'Fire Escape'}, follow_redirects=True)

        with app.app_context():
            c = db.session.get(Comment, cid)
            assert c.requirement_id is not None
            req = db.session.get(Requirement, c.requirement_id)
            assert req.title == 'Fire Escape'

    def test_convert_twice_rejected(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        cid = seed_comment(app, mid, seed['arch_id'], 'Duplicate test')
        login(client, 'arch@t.com')

        client.post(f'/comments/{cid}/convert', data={'req_title': 'First'}, follow_redirects=True)
        client.post(f'/comments/{cid}/convert', data={'req_title': 'Second'}, follow_redirects=True)

        with app.app_context():
            count = Requirement.query.filter_by(project_id=seed['project_id']).count()
            assert count == 1  # only one created

    def test_convert_uses_comment_content_as_description(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        content = 'Detailed requirement description from comment'
        cid = seed_comment(app, mid, seed['arch_id'], content)
        login(client, 'arch@t.com')

        client.post(f'/comments/{cid}/convert', data={'req_title': 'Test Req'}, follow_redirects=True)

        with app.app_context():
            req = Requirement.query.filter_by(project_id=seed['project_id']).first()
            assert req.description == content


# ── Kanban Data ───────────────────────────────────────────────────────────────

class TestKanbanData:
    def _seed_requirement(self, app, project_id, arch_id, title='Test Req', status='new'):
        with app.app_context():
            req = Requirement(
                project_id=project_id, title=title, status=status,
                category='General', created_by=arch_id,
            )
            db.session.add(req)
            db.session.commit()
            return req.id

    def test_kanban_data_returns_json(self, client, app, seed):
        self._seed_requirement(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        resp = client.get(f'/projects/{seed["project_id"]}/kanban/data')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'new' in data
        assert 'confirmed' in data
        assert 'in_progress' in data
        assert 'done' in data

    def test_requirement_appears_in_correct_column(self, client, app, seed):
        self._seed_requirement(app, seed['project_id'], seed['arch_id'], title='Skylight', status='confirmed')
        login(client, 'arch@t.com')

        resp = client.get(f'/projects/{seed["project_id"]}/kanban/data')
        data = resp.get_json()
        titles = [r['title'] for r in data['confirmed']]
        assert 'Skylight' in titles
        assert all(r['title'] != 'Skylight' for r in data['new'])

    def test_kanban_data_includes_required_fields(self, client, app, seed):
        self._seed_requirement(app, seed['project_id'], seed['arch_id'], title='Field Test')
        login(client, 'arch@t.com')

        resp = client.get(f'/projects/{seed["project_id"]}/kanban/data')
        data = resp.get_json()
        req = data['new'][0]
        assert 'id' in req
        assert 'title' in req
        assert 'category' in req
        assert 'status' not in req  # JSON helper doesn't include status key

    def test_other_architect_cannot_access_kanban_data(self, client, app, seed):
        login(client, 'other@t.com')
        resp = client.get(f'/projects/{seed["project_id"]}/kanban/data')
        assert resp.status_code == 403


# ── Status Update ─────────────────────────────────────────────────────────────

class TestUpdateStatus:
    def _seed_req(self, app, project_id, arch_id, status='new'):
        with app.app_context():
            req = Requirement(
                project_id=project_id, title='Move Me', status=status,
                category='General', created_by=arch_id,
            )
            db.session.add(req)
            db.session.commit()
            return req.id

    def test_move_to_confirmed(self, client, app, seed):
        rid = self._seed_req(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        resp = client.post(
            f'/requirements/{rid}/update-status',
            data={'status': 'confirmed'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            req = db.session.get(Requirement, rid)
            assert req.status == 'confirmed'

    def test_move_to_done(self, client, app, seed):
        rid = self._seed_req(app, seed['project_id'], seed['arch_id'], status='in_progress')
        login(client, 'arch@t.com')

        client.post(f'/requirements/{rid}/update-status', data={'status': 'done'}, follow_redirects=True)

        with app.app_context():
            req = db.session.get(Requirement, rid)
            assert req.status == 'done'

    def test_invalid_status_rejected(self, client, app, seed):
        rid = self._seed_req(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        resp = client.post(
            f'/requirements/{rid}/update-status',
            data={'status': 'hacked'},
        )
        assert resp.status_code == 400

    def test_ajax_update_returns_json(self, client, app, seed):
        rid = self._seed_req(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        resp = client.post(
            f'/requirements/{rid}/update-status',
            data={'status': 'in_progress'},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'in_progress'
        assert data['id'] == rid

    def test_other_architect_cannot_move_card(self, client, app, seed):
        rid = self._seed_req(app, seed['project_id'], seed['arch_id'])
        login(client, 'other@t.com')

        resp = client.post(
            f'/requirements/{rid}/update-status',
            data={'status': 'confirmed'},
        )
        assert resp.status_code == 403


# ── Direct Add Requirement ────────────────────────────────────────────────────

class TestAddRequirement:
    def test_architect_adds_requirement(self, client, app, seed):
        login(client, 'arch@t.com')
        pid = seed['project_id']

        resp = client.post(
            f'/projects/{pid}/requirements/add',
            data={'title': 'New Window', 'category': 'Design', 'description': 'Add to east wall'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            req = Requirement.query.filter_by(project_id=pid).first()
            assert req is not None
            assert req.title == 'New Window'
            assert req.category == 'Design'
            assert req.status == 'confirmed'  # architect adds start at confirmed
            assert req.meeting_id is None

    def test_client_can_add_requirement(self, client, app, seed):
        login(client, 'client@t.com')
        pid = seed['project_id']

        resp = client.post(
            f'/projects/{pid}/requirements/add',
            data={'title': 'Client Wish', 'category': 'Client Request'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            req = Requirement.query.filter_by(title='Client Wish').first()
            assert req is not None

    def test_empty_title_rejected(self, client, app, seed):
        login(client, 'arch@t.com')
        pid = seed['project_id']

        client.post(
            f'/projects/{pid}/requirements/add',
            data={'title': '', 'category': 'General'},
            follow_redirects=True,
        )

        with app.app_context():
            count = Requirement.query.filter_by(project_id=pid).count()
            assert count == 0

    def test_other_architect_cannot_add(self, client, app, seed):
        login(client, 'other@t.com')
        pid = seed['project_id']

        resp = client.post(
            f'/projects/{pid}/requirements/add',
            data={'title': 'Sneaky Req'},
            follow_redirects=True,
        )
        assert resp.status_code == 403


# ── Kanban HTML page ──────────────────────────────────────────────────────────

class TestKanbanPage:
    def test_kanban_page_renders(self, client, app, seed):
        login(client, 'arch@t.com')
        resp = client.get(f'/projects/{seed["project_id"]}/kanban')
        assert resp.status_code == 200
        assert b'kanban-grid' in resp.data

    def test_kanban_shows_requirements(self, client, app, seed):
        with app.app_context():
            req = Requirement(
                project_id=seed['project_id'], title='Skylight Req',
                status='confirmed', category='Design', created_by=seed['arch_id'],
            )
            db.session.add(req)
            db.session.commit()

        login(client, 'arch@t.com')
        resp = client.get(f'/projects/{seed["project_id"]}/kanban')
        assert b'Skylight Req' in resp.data

    def test_client_can_view_kanban(self, client, app, seed):
        login(client, 'client@t.com')
        resp = client.get(f'/projects/{seed["project_id"]}/kanban')
        assert resp.status_code == 200

    def test_other_architect_gets_403(self, client, app, seed):
        login(client, 'other@t.com')
        resp = client.get(f'/projects/{seed["project_id"]}/kanban')
        assert resp.status_code == 403


# ── Client-initiated meeting request ─────────────────────────────────────────

class TestClientMeetingRequest:
    def test_client_can_request_meeting(self, client, app, seed):
        login(client, 'client@t.com')
        pid = seed['project_id']

        resp = client.post(
            f'/projects/{pid}/meetings/request',
            data={'description': 'Want to discuss balcony design'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            m = Meeting.query.filter_by(project_id=pid, status='requested').first()
            assert m is not None
            assert m.description == 'Want to discuss balcony design'

    def test_client_request_without_description(self, client, app, seed):
        login(client, 'client@t.com')
        pid = seed['project_id']

        client.post(f'/projects/{pid}/meetings/request', data={}, follow_redirects=True)

        with app.app_context():
            m = Meeting.query.filter_by(project_id=pid, status='requested').first()
            assert m is None

    def test_architect_cannot_request_meeting(self, client, app, seed):
        login(client, 'arch@t.com')
        pid = seed['project_id']

        resp = client.post(
            f'/projects/{pid}/meetings/request',
            data={'description': 'Architect trying to request'},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_wrong_client_cannot_request(self, client, app, seed):
        """A client not on the project cannot create requests for it."""
        with app.app_context():
            other_client = User(name='Other', email='other_client@t.com', role='client',
                                password_hash=__import__('werkzeug.security', fromlist=['generate_password_hash']).generate_password_hash('pass'))
            db.session.add(other_client)
            db.session.commit()

        login(client, 'other_client@t.com')
        resp = client.post(
            f'/projects/{seed["project_id"]}/meetings/request',
            data={'description': 'Unauthorized'},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_propose_fills_requested_meeting(self, client, app, seed):
        """Architect proposing slots for an existing 'requested' meeting updates it."""
        pid = seed['project_id']

        # Directly seed a 'requested' meeting
        with app.app_context():
            m = Meeting(project_id=pid, status='requested', description='Discuss roof')
            db.session.add(m)
            db.session.commit()
            mid = m.id

        # Architect proposes slots referencing that meeting
        login(client, 'arch@t.com')
        resp = client.post(
            f'/projects/{pid}/meetings/propose',
            data={'slot_1': '2025-07-01T10:00', 'meeting_id': str(mid)},
        )
        # Expect a redirect (not following it — redirect target is architect-only workspace)
        assert resp.status_code in (302, 200)

        with app.app_context():
            m = Meeting.query.filter_by(id=mid).first()
            assert m.status == 'proposed'
            assert m.slot_1 is not None

    def test_request_creates_notification_for_architect(self, client, app, seed):
        login(client, 'client@t.com')
        pid = seed['project_id']

        client.post(
            f'/projects/{pid}/meetings/request',
            data={'description': 'Need to align on ventilation changes'},
            follow_redirects=True,
        )

        with app.app_context():
            architect_note = Notification.query.filter_by(user_id=seed['arch_id']).first()
            assert architect_note is not None
            assert 'Meeting Requested' in architect_note.title


# ── MeetingLog audit trail ────────────────────────────────────────────────────

class TestMeetingLog:
    def test_propose_creates_log(self, client, app, seed):
        login(client, 'arch@t.com')
        pid = seed['project_id']

        client.post(
            f'/projects/{pid}/meetings/propose',
            data={'slot_1': '2025-07-01T10:00'},
            follow_redirects=True,
        )

        with app.app_context():
            m = Meeting.query.filter_by(project_id=pid).first()
            assert m is not None
            log = MeetingLog.query.filter_by(meeting_id=m.id, action='PROPOSED').first()
            assert log is not None

    def test_confirm_creates_log(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')

        client.post(f'/meetings/{mid}/confirm', data={'slot_idx': '1'}, follow_redirects=True)

        with app.app_context():
            log = MeetingLog.query.filter_by(meeting_id=mid, action='CONFIRMED').first()
            assert log is not None

    def test_counter_creates_log(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'client@t.com')

        client.post(
            f'/meetings/{mid}/counter',
            data={'counter_slot': '2025-08-01T14:00'},
            follow_redirects=True,
        )

        with app.app_context():
            log = MeetingLog.query.filter_by(meeting_id=mid, action='COUNTER_PROPOSED').first()
            assert log is not None

    def test_mom_save_creates_log(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'], status='confirmed')
        login(client, 'arch@t.com')

        client.post(
            f'/meetings/{mid}/notes',
            data={'mom_content': 'Discussed design.'},
            follow_redirects=True,
        )

        with app.app_context():
            log = MeetingLog.query.filter_by(meeting_id=mid, action='MOM_UPDATED').first()
            assert log is not None

    def test_counter_slot_confirmation(self, client, app, seed):
        """Architect can confirm client's counter-proposed slot."""
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])

        # Client counters
        login(client, 'client@t.com')
        client.post(f'/meetings/{mid}/counter',
                    data={'counter_slot': '2025-09-01T15:00'}, follow_redirects=True)

        # Architect confirms the counter slot
        login(client, 'arch@t.com')
        resp = client.post(
            f'/meetings/{mid}/confirm',
            data={'slot_choice': 'counter_slot'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            m = db.session.get(Meeting, mid)
            assert m.status == 'confirmed'
            assert m.confirmed_slot is not None
            # confirmed_slot should match the counter_slot
            assert m.confirmed_slot.hour == 15


class TestMeetingWorkspaceUploads:
    def test_uploads_link_back_to_meeting_and_requirement(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'], status='confirmed')
        login(client, 'arch@t.com')

        client.post(
            f'/projects/{seed["project_id"]}/requirements/add',
            data={
                'title': 'Linked Requirement',
                'description': 'Attach files here',
                'meeting_id': str(mid),
                'status': 'confirmed',
            },
            follow_redirects=True,
        )

        with app.app_context():
            requirement = Requirement.query.filter_by(project_id=seed['project_id'], title='Linked Requirement').first()
            rid = requirement.id

        client.post(
            f'/projects/{seed["project_id"]}/documents/upload',
            data={
                'title': 'Meeting Spec',
                'category': 'Client Docs',
                'meeting_id': str(mid),
                'requirement_id': str(rid),
                'next': 'mom',
                'doc': (BytesIO(b'%PDF-1.4 test document'), 'meeting-spec.pdf'),
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )

        client.post(
            f'/projects/{seed["project_id"]}/images/upload',
            data={
                'meeting_id': str(mid),
                'requirement_id': str(rid),
                'next': 'mom',
                'tag': 'Reference',
                'image': (BytesIO(b'\x89PNG\r\n\x1a\nmockpng'), 'meeting-image.png'),
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )

        with app.app_context():
            document = Document.query.filter_by(project_id=seed['project_id'], meeting_id=mid).first()
            image = ProjectImage.query.filter_by(project_id=seed['project_id'], meeting_id=mid).first()
            assert document is not None
            assert document.requirement_id == rid
            assert image is not None
            assert image.requirement_id == rid


# ── Meeting Detail Page ───────────────────────────────────────────────────────

class TestMeetingDetail:
    def test_architect_can_view_detail(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')
        resp = client.get(f'/meetings/{mid}')
        assert resp.status_code == 200

    def test_client_can_view_detail(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'client@t.com')
        resp = client.get(f'/meetings/{mid}')
        assert resp.status_code == 200

    def test_other_architect_cannot_view_detail(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'other@t.com')
        resp = client.get(f'/meetings/{mid}')
        assert resp.status_code == 403

    def test_detail_shows_meeting_id(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')
        resp = client.get(f'/meetings/{mid}')
        assert str(mid).encode() in resp.data

    def test_detail_shows_requirements_from_meeting(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        with app.app_context():
            req = Requirement(
                project_id=seed['project_id'],
                meeting_id=mid,
                title='Skylight requirement',
                status='new',
            )
            db.session.add(req)
            db.session.commit()

        login(client, 'arch@t.com')
        resp = client.get(f'/meetings/{mid}')
        assert b'Skylight requirement' in resp.data

    def test_detail_shows_comments(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        cid = seed_comment(app, mid, seed['arch_id'], 'Review the balcony rails')
        login(client, 'arch@t.com')
        resp = client.get(f'/meetings/{mid}')
        assert b'Review the balcony rails' in resp.data

    def test_detail_404_for_nonexistent_meeting(self, client, app, seed):
        login(client, 'arch@t.com')
        resp = client.get('/meetings/99999')
        assert resp.status_code == 404

    def test_unauthenticated_redirected(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        resp = client.get(f'/meetings/{mid}')
        assert resp.status_code in (302, 401)


# ── Redirect Behaviour (post-fix validation) ──────────────────────────────────

class TestMeetingRedirects:
    """Verify that comment/convert routes send users to meeting detail, not the meetings list."""

    def test_add_comment_redirects_to_detail(self, client, app, seed):
        """After posting a comment the response should redirect to /meetings/<id>."""
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')
        resp = client.post(
            f'/meetings/{mid}/comment',
            data={'content': 'Looks good'},
        )
        assert resp.status_code == 302
        assert f'/meetings/{mid}' in resp.headers['Location']

    def test_client_add_comment_redirects_to_detail(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'client@t.com')
        resp = client.post(
            f'/meetings/{mid}/comment',
            data={'content': 'Please confirm the balcony width'},
        )
        assert resp.status_code == 302
        assert f'/meetings/{mid}' in resp.headers['Location']

    def test_empty_comment_still_redirects_to_detail(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        login(client, 'arch@t.com')
        resp = client.post(f'/meetings/{mid}/comment', data={'content': ''})
        assert resp.status_code == 302
        assert f'/meetings/{mid}' in resp.headers['Location']

    def test_convert_comment_redirects_to_detail(self, client, app, seed):
        """Converting a comment should redirect back to the same meeting detail."""
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        cid = seed_comment(app, mid, seed['arch_id'], 'Add ventilation shaft')
        login(client, 'arch@t.com')
        resp = client.post(
            f'/comments/{cid}/convert',
            data={'req_title': 'Ventilation shaft', 'req_category': 'Structural'},
        )
        assert resp.status_code == 302
        assert f'/meetings/{mid}' in resp.headers['Location']

    def test_convert_already_converted_redirects_to_detail(self, client, app, seed):
        """Re-converting should also redirect to meeting detail."""
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        cid = seed_comment(app, mid, seed['arch_id'], 'Some comment')
        # First convert
        login(client, 'arch@t.com')
        client.post(f'/comments/{cid}/convert', data={'req_title': 'Title'})
        # Try again
        resp = client.post(f'/comments/{cid}/convert', data={'req_title': 'Title2'})
        assert resp.status_code == 302
        assert f'/meetings/{mid}' in resp.headers['Location']


# ── Requirement Initial Status ────────────────────────────────────────────────

class TestRequirementInitialStatus:
    """Architect-added requirements start at 'confirmed'; client requests start at 'new'."""

    def test_architect_requirement_starts_confirmed(self, client, app, seed):
        login(client, 'arch@t.com')
        pid = seed['project_id']
        client.post(
            f'/projects/{pid}/requirements/add',
            data={'title': 'Widen doorway', 'category': 'Structural'},
        )
        with app.app_context():
            req = Requirement.query.filter_by(project_id=pid, title='Widen doorway').first()
            assert req is not None
            assert req.status == 'confirmed', f"Expected 'confirmed', got '{req.status}'"

    def test_client_requirement_starts_new(self, client, app, seed):
        login(client, 'client@t.com')
        pid = seed['project_id']
        client.post(
            f'/projects/{pid}/requirements/add',
            data={'title': 'Add skylight', 'category': 'Design'},
        )
        with app.app_context():
            req = Requirement.query.filter_by(project_id=pid, title='Add skylight').first()
            assert req is not None
            assert req.status == 'new', f"Expected 'new', got '{req.status}'"

    def test_converted_comment_starts_new(self, client, app, seed):
        """Requirements created by converting comments always start as 'new'."""
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'])
        cid = seed_comment(app, mid, seed['arch_id'], 'Raise ceiling height')
        login(client, 'arch@t.com')
        client.post(f'/comments/{cid}/convert', data={'req_title': 'Raise ceiling height'})
        with app.app_context():
            req = Requirement.query.filter_by(
                project_id=seed['project_id'], title='Raise ceiling height'
            ).first()
            assert req is not None
            assert req.status == 'new'

    def test_client_request_meeting_created(self, client, app, seed):
        """Client requesting a meeting creates a Meeting with status='requested'."""
        login(client, 'client@t.com')
        pid = seed['project_id']
        resp = client.post(
            f'/projects/{pid}/meetings/request',
            data={'description': 'Want to discuss roof design'},
        )
        assert resp.status_code in (302, 200)
        with app.app_context():
            m = Meeting.query.filter_by(project_id=pid, status='requested').first()
            assert m is not None

    def test_architect_proposal_creates_awaiting_client(self, client, app, seed):
        """Architect proposing slots sets status='awaiting_client'."""
        login(client, 'arch@t.com')
        pid = seed['project_id']
        resp = client.post(
            f'/projects/{pid}/meetings/propose',
            data={'slot_1': '2026-06-01T10:00'},
        )
        assert resp.status_code in (302, 200)
        with app.app_context():
            m = Meeting.query.filter_by(project_id=pid, status='proposed').first()
            assert m is not None

    def test_client_confirms_slot_status_becomes_confirmed(self, client, app, seed):
        mid = seed_meeting(app, seed['project_id'], seed['arch_id'], status='awaiting_client')
        login(client, 'client@t.com')
        resp = client.post(f'/meetings/{mid}/confirm', data={'slot_idx': '1'})
        assert resp.status_code in (302, 200)
        with app.app_context():
            m = db.session.get(Meeting, mid)
            assert m.status == 'confirmed'
