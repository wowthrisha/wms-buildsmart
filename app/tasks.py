import os
from celery import Celery

# Initialize Celery with broker URL (Redis by default)
broker_url = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
celery = Celery(__name__, broker=broker_url)

@celery.task
def async_send_whatsapp(phone, message):
    from app.notifications_service import send_whatsapp
    import logging
    try:
        send_whatsapp(phone, message)
    except Exception as e:
        logging.getLogger(__name__).error(f"Async WhatsApp failed: {e}")

@celery.task
def async_send_email(to_email, subject, body):
    from app.notifications_service import send_email
    import logging
    try:
        send_email(to_email, subject, body)
    except Exception as e:
        logging.getLogger(__name__).error(f"Async Email failed: {e}")

@celery.task
def process_visual_reference(reference_id):
    """Background task to run Vision + NLP + Fusion on a VisualReference."""
    from app import db
    from app.models import VisualReference, RequirementCard
    from app.vision import get_vision_pipeline
    from app.rag import RAGManager
    from app.requirement_fusion import fuse_requirements
    from flask import current_app
    import os
    import json
    import logging

    try:
        ref = db.session.get(VisualReference, reference_id)
        if not ref:
            return

        # 1. Vision (CLIP)
        vision = get_vision_pipeline()
        image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], ref.filename)
        vision_results = vision.analyze_image(image_path)
        
        # 2. NLP (RAG Intent)
        rag = RAGManager.get_instance()
        nlp_intent = rag.extract_requirement_intent(ref.caption)
        
        # 3. Fusion
        fusion = fuse_requirements(vision_results, nlp_intent)
        
        # 4. Save to DB
        card = RequirementCard(
            visual_reference_id=ref.id,
            vision_tags_json=json.dumps(fusion['vision_tags']),
            extracted_intent=nlp_intent,
            fused_label=fusion['fused_label'],
            divergence_score=fusion['divergence_score']
        )
        db.session.add(card)
        db.session.commit()
        
    except Exception as e:
        logging.getLogger(__name__).error(f"Image Board Pipeline Failed: {e}")
