from app import create_app, db
from app.compliance_service import ensure_project_compliance_items
from app.models import User, Project
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
        ensure_project_compliance_items(p.id)
        db.session.commit()

    print("Database initialized with arch@qa.com and client@qa.com")
