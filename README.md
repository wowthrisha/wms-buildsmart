# BuildSmart: Professional Architectural SaaS

BuildSmart is a production-grade SaaS system designed for architects and their clients. It streamlines the entire project lifecycle—from compliance checking (TNPCR-2019) to real-time progress tracking and visual reference management.

## 🚀 Vision
To empower architects with AI-driven compliance and visual reasoning, while providing clients with a transparent, real-time portal for their dream projects.

## 🛠️ Core Technology Stack
- **Backend**: Flask (Python 3.13+)
- **Database**: PostgreSQL (Production) / SQLite (Development)
- **Real-time**: Redis PubSub + Server-Sent Events (SSE)
- **Background Tasks**: Celery + Redis
- **AI Engine**: 
  - **Vision**: CLIP (OpenAI) for visual reference analysis.
  - **NLP**: LangChain + Gemini Pro for RAG-based compliance intent extraction.
  - **OCR**: Tesseract + OpenCV for automated dimension and text extraction.
- **Messaging**: Twilio (WhatsApp) & Resend (Email)

## 📁 Repository Structure
```text
buildsmart/
├── app/                # Core Application Logic
│   ├── routes/         # Blueprints (Auth, Projects, Client, etc.)
│   ├── models.py       # SQLAlchemy Data Models
│   └── templates/      # Jinja2 Frontend Templates
├── instance/           # Local SQLite Database & Config Override
├── scripts/            # Deployment & Maintenance Shell Scripts
├── tests/              # Pytest Suite (Unit & Integration)
├── tools/              # Debug & Utility Python Scripts (init_db, etc.)
├── config.py           # Application Configuration
├── requirements.txt    # Python Dependencies
└── run.py              # Application Entry Point
```

## ⚡ Quickstart
1. **Setup Environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Configure Services**:
   Copy `.env.example` to `.env` and provide your API keys.
3. **Initialize Database**:
   ```bash
   PYTHONPATH=. python tools/init_db.py
   ```
4. **Run the Server**:
   ```bash
   python run.py
   ```

## 🧪 Documentation
For detailed technical guides, setup for AI pipelines, and testing procedures, see [DEVELOPMENT.md](./DEVELOPMENT.md).
