import json
import logging
from datetime import datetime
from collections import Counter

from app import db
from app.models import VisualReference, RequirementCard
from app.vision import compute_divergence_score
from app.rag import extract_requirement_intent

_MATERIAL_KEYWORDS = [
    'wood', 'concrete', 'marble', 'brick', 'stone',
    'teak', 'glass', 'steel', 'terracotta', 'tile',
]


def generate_requirement_card(project_id):
    """
    Fusion Layer — combines CLIP vision output (Pipeline 1) and NLP intent
    extraction (Pipeline 2) into a single per-project RequirementCard.

    Called after every new VisualReference upload.
    Creates or updates the single card for the project.

    Returns the RequirementCard, or None if no references exist.
    """
    refs = VisualReference.query.filter_by(project_id=project_id).all()
    if not refs:
        return None

    client_refs = [r for r in refs if r.uploader_role == 'client']
    arch_refs   = [r for r in refs if r.uploader_role == 'architect']

    all_client_tags, all_arch_tags = [], []
    all_conflicts, feasibility_scores = [], []
    all_materials, all_spatial, nlp_intents = [], [], []

    for ref in refs:
        tags = json.loads(ref.tags_json or '[]')

        if ref.uploader_role == 'client':
            all_client_tags.extend(tags[:3])
        else:
            all_arch_tags.extend(tags[:3])

        # Collect material keywords from style tags
        for tag in tags:
            for mat in _MATERIAL_KEYWORDS:
                if mat in tag.lower():
                    all_materials.append(mat)

        # NLP pipeline on caption
        if ref.caption and ref.caption.strip():
            try:
                intent = extract_requirement_intent(ref.caption, project_id)
                all_conflicts.extend(intent.get('compliance_conflicts', []))
                score = intent.get('feasibility_score', 50)
                if isinstance(score, (int, float)):
                    feasibility_scores.append(score)
                all_spatial.extend(intent.get('spatial_features', []))
                summary = intent.get('intent_summary', '')
                if summary:
                    nlp_intents.append(summary)
            except Exception as e:
                logging.warning(f"[fusion] NLP error for ref {ref.id}: {e}")

    divergence    = compute_divergence_score(client_refs, arch_refs)
    avg_feasibility = (
        round(sum(feasibility_scores) / len(feasibility_scores), 1)
        if feasibility_scores else 75.0
    )
    visual_style  = _primary_style(all_client_tags or all_arch_tags)

    card = RequirementCard.query.filter_by(project_id=project_id).first()
    if card is None:
        card = RequirementCard(project_id=project_id)
        db.session.add(card)

    card.visual_style    = visual_style
    card.materials_json  = json.dumps(list(set(all_materials)))
    card.spatial_tags    = json.dumps(list(set(all_spatial)))
    card.nlp_intent      = json.dumps(nlp_intents)
    card.conflicts_json  = json.dumps(list(set(all_conflicts)))
    card.divergence_score = divergence
    card.feasibility_pct  = avg_feasibility
    card.generated_at     = datetime.utcnow()

    db.session.commit()
    return card


def _primary_style(tags):
    """Return a short human-readable style label from the most common tag."""
    if not tags:
        return 'Not determined'
    most_common = Counter(tags).most_common(1)[0][0]
    label = (most_common
             .replace('an interior with ', '')
             .replace('a interior with ', '')
             .replace('a ', '')
             .replace('an ', ''))
    return label[:80]


# ── Legacy shim ───────────────────────────────────────────────────────────────
def fuse_requirements(vision_results, nlp_intent):
    """
    Kept for backward-compat with the old tasks.py Celery task.
    The real logic now lives in generate_requirement_card().
    """
    try:
        v_tags  = [item['tag'].lower() for item in vision_results]
        n_tokens = [t.strip().lower() for t in nlp_intent.split(',')]
        common   = set(v_tags).intersection(set(n_tokens))
        total    = len(set(v_tags).union(set(n_tokens)))
        divergence = 1.0 - (len(common) / total) if total else 0.5
        fused_label = (
            f"Confirmed: {', '.join(common)}" if common
            else f"{v_tags[0] if v_tags else 'Visual'} vs {n_tokens[0] if n_tokens else 'Intent'}"
        )
        return {'fused_label': fused_label, 'divergence_score': divergence,
                'vision_tags': v_tags, 'nlp_intent': n_tokens}
    except Exception as e:
        logging.error(f"[fusion] fuse_requirements error: {e}")
        return {'fused_label': 'Analysis Pending', 'divergence_score': 1.0,
                'vision_tags': [], 'nlp_intent': []}
