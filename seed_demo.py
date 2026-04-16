"""
seed_demo.py — Create 3 clients, 3 projects, and upload sample docs.
Run from buildsmart/ directory:
    PYTHONPATH=. python seed_demo.py
"""
import os, shutil, uuid
from werkzeug.security import generate_password_hash

SAMPLE_DIR = "/Users/thrisha/BS_APP/sample demo docs"

CLIENTS = [
    {"name": "Sharma Residence",   "email": "sharma@demo.com",   "phone": "+91 98400 11111"},
    {"name": "Vikram Builders",    "email": "vikram@demo.com",    "phone": "+91 98400 22222"},
    {"name": "Priya Construction", "email": "priya@demo.com",     "phone": "+91 98400 33333"},
]

PROJECTS = [
    {"name": "Sharma Residence — G+2",      "plot_zone": "Residential", "status": "Design",     "budget": 4500000},
    {"name": "Vikram Commercial Complex",    "plot_zone": "Commercial",  "status": "Review",     "budget": 12000000},
    {"name": "Priya Layout — Phase 1",       "plot_zone": "Residential", "status": "Approved",   "budget": 7800000},
]

# Map sample files to doc categories for each project
DOC_SETS = [
    # Sharma: structural + legal docs
    [
        ("Terrace Floor Beam Layout.pdf",                        "Structural",  "terrace_beam_layout"),
        ("denial ba 1046 11.10.25.pdf",                          "Legal",       "approval_letter"),
        ("denial ba 1325 4.12.25.pdf",                           "Legal",       "approval_letter"),
        ("WhatsApp Image 2026-04-01 at 9.10.48 AM.jpeg",         "Site Plan",   "site_photo"),
    ],
    # Vikram: financial + inventory
    [
        ("Inventory.pdf",                                         "Client Docs", "inventory"),
        ("Sales_ASM-2032_25-26.pdf",                             "Client Docs", "sales_statement"),
        ("Sales_ASM-2586_25-26.pdf",                             "Client Docs", "sales_statement"),
        ("Vijayadeepa Costructions Pvt Ltd. _EAEST 25-26 704_01-09-2025.pdf", "Client Docs", "assessment"),
        ("gi.jpg",                                               "Site Plan",   "site_photo"),
    ],
    # Priya: regulations + field photos
    [
        ("TNCDBR-2019 (3) (1).pdf",                              "Legal",       "regulation_doc"),
        ("daniel ba 1566 5.1.26.pdf",                            "Legal",       "approval_letter"),
        ("denial ba 1636 20.1.26.pdf",                           "Legal",       "approval_letter"),
        ("Sales_ASM-2736_25-26.pdf",                             "Client Docs", "sales_statement"),
        ("WhatsApp Image 2026-04-01 at 9.10.48 AM (1).jpeg",     "Site Plan",   "site_photo"),
    ],
]

def run():
    from app import create_app, db
    from app.models import User, Project, Document, DocumentVersion
    from app.compliance_service import ensure_project_compliance_items
    from app.time_utils import utc_now

    app = create_app()

    with app.app_context():
        upload_folder = app.config['UPLOAD_FOLDER']
        os.makedirs(upload_folder, exist_ok=True)

        # Architect
        arch = User.query.filter_by(email='arch@qa.com').first()
        if not arch:
            print("ERROR: Run 'flask seed' first to create arch@qa.com")
            return

        created_clients = []
        created_projects = []

        for i, cdata in enumerate(CLIENTS):
            client = User.query.filter_by(email=cdata['email']).first()
            if not client:
                client = User(
                    name=cdata['name'],
                    email=cdata['email'],
                    phone=cdata['phone'],
                    role='client',
                    password_hash=generate_password_hash('qapass123'),
                )
                db.session.add(client)
                db.session.flush()
                print(f"  Created client: {cdata['email']}")
            else:
                print(f"  Client already exists: {cdata['email']}")
            created_clients.append(client)

        db.session.commit()

        for i, (client, pdata) in enumerate(zip(created_clients, PROJECTS)):
            proj = Project.query.filter_by(name=pdata['name']).first()
            if not proj:
                proj = Project(
                    name=pdata['name'],
                    architect_id=arch.id,
                    client_id=client.id,
                    plot_zone=pdata['plot_zone'],
                    status=pdata['status'],
                    total_budget=pdata['budget'],
                )
                db.session.add(proj)
                db.session.flush()
                ensure_project_compliance_items(proj.id)
                db.session.commit()
                print(f"  Created project: {pdata['name']}")
            else:
                print(f"  Project already exists: {pdata['name']}")
            created_projects.append(proj)

        # Upload documents
        for proj, doc_set in zip(created_projects, DOC_SETS):
            for filename, category, doc_type in doc_set:
                src = os.path.join(SAMPLE_DIR, filename)
                if not os.path.exists(src):
                    print(f"  SKIP (not found): {filename}")
                    continue

                # Check if already uploaded for this project
                existing = Document.query.filter_by(project_id=proj.id, original_name=filename).first()
                if existing:
                    print(f"  Already uploaded: {filename} → {proj.name}")
                    continue

                ext = os.path.splitext(filename)[1].lower()
                safe_name = f"{uuid.uuid4().hex}{ext}"
                dst = os.path.join(upload_folder, safe_name)
                shutil.copy2(src, dst)

                file_size = os.path.getsize(dst)
                size_str = f"{file_size // 1024} KB" if file_size < 1024*1024 else f"{file_size // (1024*1024)} MB"

                mime_map = {'.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png'}
                mime = mime_map.get(ext, 'application/octet-stream')

                doc = Document(
                    project_id=proj.id,
                    filename=safe_name,
                    original_name=filename,
                    category=category,
                    doc_type=doc_type,
                    uploader_id=arch.id,
                    uploaded_by=arch.id,
                    uploader_role='architect',
                    source_module='vault',
                    visible_to_client=True,
                    approval_status='none',
                    created_at=utc_now(),
                )
                db.session.add(doc)
                db.session.flush()

                ver = DocumentVersion(
                    document_id=doc.id,
                    filename=safe_name,
                    version_num=1,
                    version_label='Rev A',
                    file_type=mime,
                    file_size=size_str,
                    uploader_id=arch.id,
                    created_at=utc_now(),
                )
                db.session.add(ver)
                db.session.commit()
                print(f"  Uploaded: {filename} → {proj.name} [{category}]")

        print("\nDone. Credentials (password: qapass123):")
        print("  Architect:  arch@qa.com")
        for c in created_clients:
            print(f"  Client:     {c.email}")

if __name__ == '__main__':
    run()
