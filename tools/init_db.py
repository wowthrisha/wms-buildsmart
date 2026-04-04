from app import create_app, db
from app.models import User, Project, ComplianceItem
from werkzeug.security import generate_password_hash

app = create_app()
with app.app_context():
    db.drop_all()
    db.create_all()
    
    # Create Architect
    arch = User(name='Architect One', email='arch@qa.com', role='architect', email_verified=True)
    arch.set_password('qapass123')
    db.session.add(arch)
    
    # Create Client
    client = User(name='Client One', email='client@qa.com', role='client', email_verified=True)
    client.set_password('qapass123')
    db.session.add(client)
    
    db.session.commit()
    
    # Create a project
    p = Project(name='Demo Project', architect_id=arch.id, client_id=client.id, plot_zone='Mixed')
    db.session.add(p)
    db.session.commit()
    
    # Add default items
    for label in ['Building Plan Approval', 'Fire NOC']:
        db.session.add(ComplianceItem(project_id=p.id, label=label))
    db.session.commit()

    print("Database initialized with arch@qa.com and client@qa.com")
