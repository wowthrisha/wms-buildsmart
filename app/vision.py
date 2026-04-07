import json
import os
import torch
from PIL import Image
from functools import lru_cache

# ──────────────────────────────────────────────
# STYLE PROMPTS — Tamil Nadu + International (46)
# ──────────────────────────────────────────────
STYLE_PROMPTS = [
    # International styles
    'a minimalist modern interior with clean lines',
    'an industrial loft with exposed concrete walls',
    'a Scandinavian interior with light wood and white',
    'a contemporary open-plan living space',
    'a luxury modern villa interior',
    'a tropical open-plan living space with greenery',
    'a double-height living room with high ceilings',
    'a compact efficient apartment layout',
    'a traditional heritage interior with ornate details',
    'a rustic farmhouse interior with natural materials',
    # Tamil Nadu / South Indian regional styles
    'a traditional Chettinad courtyard house interior',
    'a Kerala nalukettu traditional home with central courtyard',
    'a mangalore-tile roof traditional house',
    'a tropical pergola outdoor space',
    'a South Indian heritage home with wooden pillars',
    'a contemporary South Indian home with local materials',
    # Materials
    'an interior with exposed brick walls',
    'an interior with teak wood furniture and flooring',
    'an interior with marble floors and surfaces',
    'an interior with terracotta tiles',
    'an interior with stone walls and natural textures',
    'an interior with glass and steel modern design',
    'an interior with bamboo and sustainable materials',
    # Spatial features
    'a courtyard house with open sky',
    'a home with large windows and natural light',
    'a compact studio apartment layout',
    'an open kitchen connected to dining area',
    'a home with garden and outdoor living space',
    'a multi-story home with internal staircase',
    'a single-floor bungalow layout',
    # Color palettes
    'a warm interior with earthy tones and browns',
    'a cool interior with greys and blues',
    'a bright white minimal interior',
    'a dark moody interior with deep colors',
    'a colorful vibrant interior with patterns',
    # Room types
    'a modern kitchen with island counter',
    'a master bedroom with attached bathroom',
    'a home office study room',
    'a children bedroom with playful design',
    'a formal living room with traditional furniture',
    # Lighting
    'an interior with warm ambient lighting',
    'an interior with natural daylight and skylights',
    'a dramatic interior with statement lighting fixtures',
    # Budget signals
    'a budget-friendly simple interior design',
    'a premium luxury high-end interior',
    'a mid-range practical home interior',
    # Special
    'a Vastu-compliant traditional Indian home layout',
]


@lru_cache(maxsize=1)
def _load_model():
    """Load CLIP model once via transformers, cache it."""
    try:
        from transformers import CLIPProcessor, CLIPModel
        model_id = 'openai/clip-vit-base-patch32'
        model = CLIPModel.from_pretrained(model_id)
        processor = CLIPProcessor.from_pretrained(model_id)
        model.eval()
        return model, processor
    except Exception as e:
        print(f"[vision.py] CLIP load failed: {e}")
        return None, None


def analyse_image(filepath):
    """
    Run CLIP analysis on an image file.
    Returns dict with top-5 tags, scores, primary style, dominant colors.
    Falls back gracefully if CLIP or the file is unavailable.
    """
    model, processor = _load_model()

    if model is None:
        return {
            'tags': ['style-unavailable'],
            'scores': {},
            'style_primary': 'unavailable',
            'colors_json': '[]',
            'clip_scores': '{}',
            'error': 'CLIP model not available',
        }

    try:
        image = Image.open(filepath).convert('RGB')
        inputs = processor(
            text=STYLE_PROMPTS,
            images=image,
            return_tensors='pt',
            padding=True,
        )

        with torch.no_grad():
            outputs = model(**inputs)
            probs = outputs.logits_per_image.softmax(dim=1).squeeze().tolist()

        scores = dict(zip(STYLE_PROMPTS, probs))
        top_tags = sorted(scores, key=scores.get, reverse=True)[:5]
        style_primary = top_tags[0] if top_tags else 'unknown'
        colors_json = _extract_dominant_colors(filepath)

        return {
            'tags': top_tags,
            'scores': scores,
            'style_primary': style_primary,
            'colors_json': colors_json,
            'clip_scores': json.dumps({k: round(v, 4) for k, v in scores.items()}),
            'error': None,
        }

    except Exception as e:
        print(f"[vision.py] analyse_image error: {e}")
        return {
            'tags': [],
            'scores': {},
            'style_primary': 'error',
            'colors_json': '[]',
            'clip_scores': '{}',
            'error': str(e),
        }


def _extract_dominant_colors(filepath, n=5):
    """Extract n dominant colors via PIL quantization."""
    try:
        img = Image.open(filepath).convert('RGB').resize((150, 150))
        quantized = img.quantize(colors=n)
        palette = quantized.getpalette()
        colors = [
            '#{:02x}{:02x}{:02x}'.format(
                palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]
            )
            for i in range(n)
        ]
        return json.dumps(colors)
    except Exception:
        return '[]'


def compute_divergence_score(client_refs, arch_refs):
    """
    Compute style divergence (0–100 %) between client and architect refs.
    Returns None if either side is empty.
    """
    if not client_refs or not arch_refs:
        return None

    client_tags, arch_tags = set(), set()
    for ref in client_refs:
        client_tags.update(json.loads(ref.tags_json or '[]')[:3])
    for ref in arch_refs:
        arch_tags.update(json.loads(ref.tags_json or '[]')[:3])

    if not client_tags:
        return None

    divergence = (len(client_tags - arch_tags) / len(client_tags)) * 100
    return round(divergence, 1)


# Legacy singleton helper kept for backward-compat with tasks.py
def get_vision_pipeline():
    """Returns a shim that delegates to analyse_image()."""
    class _Shim:
        def analyze_image(self, path, top_n=5):
            result = analyse_image(path)
            return [{'tag': t, 'score': 0.0} for t in result.get('tags', [])]
    return _Shim()
