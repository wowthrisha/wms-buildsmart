# BuildSmart: Development Guide

This document provides technical details for developers working on the BuildSmart codebase.

## 🧠 AI Pipeline: Vision + RAG Fusion
BuildSmart uses a dual-pipeline approach for visual reference management:

1. **Vision (CLIP)**: Extracts visual features and types from reference images.
2. **NLP (RAG)**: Uses Gemini Pro to extract architectural intent from user captions, grounded in TNPCR building regulations.
3. **Fusion Layer**: Combines Vision and NLP outputs to generate a "Requirement Card" with a divergence score.

### Background Processing
Visual processing is asynchronous. When an image is uploaded:
- A `VisualReference` record is created.
- A Celery task (`process_visual_reference`) is triggered.
- Upon completion, a `RequirementCard` is linked to the reference.

## 🔔 Real-time Infrastructure
Real-time updates are powered by **Redis PubSub**:
- **Backend**: `app._notify` publishes JSON events to `notifications_<user_id>`.
- **Frontend**: Dashboard opens an SSE connection to `/notifications/stream`.

## 🛡️ Security & Auth
- **RBAC**: Multi-role access via `@roles_required(['role1', 'role2'])`.
- **CSRF**: Mandatory for all POST requests. Use `{{ csrf_token() }}` in templates.
- **Recovery**: Token-based password reset and email verification flows.

## 🧪 Testing
We use `pytest` for automated verification.
- **Unit Tests**: Located in `tests/`.
- **Integration Tests**: `tests/test_production_flows.py` simulates full user journeys.
- **Tools**: CLI utilities in `tools/` for manual testing.

### Running Tests
```bash
PYTHONPATH=. pytest tests/
```

## 🚢 Deployment
The project includes a `Procfile` for Heroku/Render and a `scripts/deploy.sh` for manual VPS deployments. Ensure `REDIS_URL` and `DATABASE_URL` are configured in the target environment.
