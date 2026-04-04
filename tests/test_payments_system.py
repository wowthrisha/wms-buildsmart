from io import BytesIO
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

from app import db
from app.models import Notification, PaymentLog, Project, User


def login(client, email, password='pass123'):
    client.get('/logout', follow_redirects=True)
    return client.post('/login', data={'email': email, 'password': password}, follow_redirects=True)


def proof_file(name='proof.pdf', payload=b'%PDF-1.4 payment proof'):
    return (BytesIO(payload), name)


def create_finance_setup(app):
    with app.app_context():
        architect = User(name='Architect Pay', email='arch-pay@test.com', role='architect', phone='+911111111111', password_hash=generate_password_hash('pass123'))
        client = User(name='Client Pay', email='client-pay@test.com', role='client', phone='+922222222222', password_hash=generate_password_hash('pass123'))
        project = Project(name='Finance Project', architect=architect, client=client, total_budget=1000)
        db.session.add_all([architect, client, project])
        db.session.commit()
        return {'architect_id': architect.id, 'client_id': client.id, 'project_id': project.id}


def test_client_payment_confirm_updates_chart(app, client, tmp_path):
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    ids = create_finance_setup(app)

    login(client, 'client-pay@test.com')
    response = client.post(
        f"/projects/{ids['project_id']}/payments/add",
        data={
            'amount': '500',
            'description': 'Initial architect fee',
            'category': 'Architect Fee',
            'stage': 'Design',
            'payment_method': 'UPI',
            'proof': proof_file(),
        },
        content_type='multipart/form-data',
        follow_redirects=True,
    )
    assert b'Payment logged and sent for architect confirmation.' in response.data

    with app.app_context():
        payment_log = PaymentLog.query.one()
        assert payment_log.status == 'pending'
        assert Notification.query.filter_by(user_id=ids['architect_id']).count() >= 1

    login(client, 'arch-pay@test.com')
    client.post(f"/payments/{payment_log.id}/approve", data={'comment': 'Matched with proof'}, follow_redirects=True)

    summary = client.get(f"/payments/project/{ids['project_id']}").get_json()['summary']
    assert summary['paid_to_architect'] == 500
    assert summary['spent_on_project'] == 0
    assert summary['remaining'] == 500


def test_architect_expense_updates_chart(app, client, tmp_path):
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    ids = create_finance_setup(app)

    login(client, 'arch-pay@test.com')
    response = client.post(
        f"/projects/{ids['project_id']}/payments/add",
        data={
            'amount': '250',
            'paid_to': 'vendor',
            'description': 'Steel advance',
            'category': 'Material',
            'stage': 'Construction',
            'payment_method': 'Bank Transfer',
            'proof': proof_file('steel.pdf'),
        },
        content_type='multipart/form-data',
        follow_redirects=True,
    )
    assert b'Expense logged and added to the shared ledger.' in response.data

    with app.app_context():
        payment_log = PaymentLog.query.one()
        assert payment_log.status == 'confirmed'
        assert payment_log.approved_by == ids['architect_id']

    summary = client.get(f"/payments/project/{ids['project_id']}").get_json()['summary']
    assert summary['paid_to_architect'] == 0
    assert summary['spent_on_project'] == 250
    assert summary['remaining'] == 750


def test_rejected_payment_removed_from_totals(app, client, tmp_path):
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    ids = create_finance_setup(app)

    login(client, 'client-pay@test.com')
    client.post(
        f"/projects/{ids['project_id']}/payments/add",
        data={
            'amount': '450',
            'description': 'Design milestone',
            'category': 'Architect Fee',
            'stage': 'Design',
            'payment_method': 'GPay',
            'proof': proof_file('mile.pdf'),
        },
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    with app.app_context():
        payment_log = PaymentLog.query.one()

    login(client, 'arch-pay@test.com')
    client.post(f"/payments/{payment_log.id}/reject", data={'comment': 'Proof mismatch'}, follow_redirects=True)

    payload = client.get(f"/payments/project/{ids['project_id']}").get_json()
    assert payload['summary']['paid_to_architect'] == 0
    assert payload['summary']['remaining'] == 1000

    with app.app_context():
        assert db.session.get(PaymentLog, payment_log.id).status == 'rejected'


def test_missing_proof_is_blocked(app, client, tmp_path):
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    ids = create_finance_setup(app)

    login(client, 'client-pay@test.com')
    response = client.post(
        f"/projects/{ids['project_id']}/payments/add",
        data={
            'amount': '400',
            'description': 'Advance',
            'category': 'Architect Fee',
            'stage': 'Concept',
            'payment_method': 'Cash',
        },
        follow_redirects=True,
    )
    assert b'Proof upload is mandatory for every payment entry.' in response.data

    with app.app_context():
        assert PaymentLog.query.count() == 0


def test_budget_exceeded_warning_shown(app, client, tmp_path):
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    ids = create_finance_setup(app)

    with app.app_context():
        project = db.session.get(Project, ids['project_id'])
        client_user = db.session.get(User, ids['client_id'])
        architect_user = db.session.get(User, ids['architect_id'])
        db.session.add_all([
            PaymentLog(project_id=project.id, amount=700, paid_by='client', paid_to='architect', description='Fee', category='Architect Fee', stage='Design', payment_method='UPI', proof_path='a.pdf', status='confirmed', created_by=client_user.id, approved_by=architect_user.id, created_at=datetime.now(timezone.utc), approved_at=datetime.now(timezone.utc)),
            PaymentLog(project_id=project.id, amount=400, paid_by='architect', paid_to='vendor', description='Vendor bill', category='Material', stage='Construction', payment_method='Cash', proof_path='b.pdf', status='confirmed', created_by=architect_user.id, approved_by=architect_user.id, created_at=datetime.now(timezone.utc), approved_at=datetime.now(timezone.utc)),
        ])
        db.session.commit()

    login(client, 'arch-pay@test.com')
    response = client.get(f"/projects/{ids['project_id']}/payments")
    assert b'Over Budget' in response.data


def test_filters_work(app, client, tmp_path):
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    ids = create_finance_setup(app)

    with app.app_context():
        client_user = db.session.get(User, ids['client_id'])
        architect_user = db.session.get(User, ids['architect_id'])
        db.session.add_all([
            PaymentLog(project_id=ids['project_id'], amount=300, paid_by='client', paid_to='architect', description='Client milestone row', category='Architect Fee', stage='Design', payment_method='UPI', proof_path='client.pdf', status='confirmed', created_by=client_user.id, approved_by=architect_user.id, created_at=datetime.now(timezone.utc), approved_at=datetime.now(timezone.utc)),
            PaymentLog(project_id=ids['project_id'], amount=200, paid_by='architect', paid_to='vendor', description='Expense-only row', category='Material', stage='Construction', payment_method='Cash', proof_path='expense.pdf', status='confirmed', created_by=architect_user.id, approved_by=architect_user.id, created_at=datetime.now(timezone.utc), approved_at=datetime.now(timezone.utc)),
        ])
        db.session.commit()

    login(client, 'arch-pay@test.com')
    response = client.get(f"/projects/{ids['project_id']}/payments?entry_type=expenses&category=Material&stage=Construction")
    assert b'Expense-only row' in response.data
    assert b'Client milestone row' not in response.data


def test_auto_confirm_works(app, client, tmp_path):
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    ids = create_finance_setup(app)

    with app.app_context():
        stale = PaymentLog(
            project_id=ids['project_id'],
            amount=350,
            paid_by='client',
            paid_to='architect',
            description='Old client payment',
            category='Architect Fee',
            stage='Concept',
            payment_method='UPI',
            proof_path='old.pdf',
            status='pending',
            created_by=ids['client_id'],
            created_at=datetime.now(timezone.utc) - timedelta(days=8),
        )
        db.session.add(stale)
        db.session.commit()
        stale_id = stale.id

    login(client, 'arch-pay@test.com')
    client.get(f"/projects/{ids['project_id']}/payments")

    with app.app_context():
        refreshed = db.session.get(PaymentLog, stale_id)
        assert refreshed.status == 'auto_confirmed'
        assert refreshed.approved_by == ids['architect_id']


def test_table_reflects_correct_data(app, client, tmp_path):
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    ids = create_finance_setup(app)

    with app.app_context():
        architect_user = db.session.get(User, ids['architect_id'])
        row = PaymentLog(
            project_id=ids['project_id'],
            amount=180,
            paid_by='architect',
            paid_to='vendor',
            description='Plumbing fix',
            category='Plumbing',
            stage='Construction',
            payment_method='Cheque',
            proof_path='plumbing.pdf',
            status='confirmed',
            comment='Architect note (01 Jan 2026): finalised',
            created_by=architect_user.id,
            approved_by=architect_user.id,
            created_at=datetime.now(timezone.utc),
            approved_at=datetime.now(timezone.utc),
        )
        db.session.add(row)
        db.session.commit()

    login(client, 'client-pay@test.com')
    response = client.get(f"/projects/{ids['project_id']}/payments")
    assert b'Plumbing' in response.data
    assert b'Construction' in response.data
    assert b'Cheque' in response.data
    assert b'Open Proof' in response.data
    assert b'Architect note' in response.data
