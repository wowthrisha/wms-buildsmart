import json
import logging

def fuse_requirements(vision_results, nlp_intent):
    """
    Combines Vision (CLIP) and NLP (RAG) data.
    Calculates a 'Divergence Score' based on how much the vision tags 
    align with the text intent.
    """
    try:
        # Extract tags from vision results
        v_tags = [item['tag'].lower() for item in vision_results]
        
        # Extract tokens from NLP intent
        n_tokens = [t.strip().lower() for t in nlp_intent.split(',')]
        
        # Intersection
        common = set(v_tags).intersection(set(n_tokens))
        
        # Calculate Divergence Score (0 to 1)
        # 1.0 means completely different, 0.0 means perfect alignment
        total_unique = len(set(v_tags).union(set(n_tokens)))
        if total_unique == 0:
            divergence = 0.5
        else:
            divergence = 1.0 - (len(common) / total_unique)
            
        # Refined Fused Label
        if len(common) > 0:
            fused_label = f"Confirmed: {', '.join(common)}"
        else:
            # Fallback to top vision tag + top NLP token
            fused_label = f"{v_tags[0] if v_tags else 'Visual'} vs {n_tokens[0] if n_tokens else 'Intent'}"
            
        return {
            "fused_label": fused_label,
            "divergence_score": divergence,
            "vision_tags": v_tags,
            "nlp_intent": n_tokens
        }
    except Exception as e:
        logging.error(f"Fusion Error: {e}")
        return {
            "fused_label": "Analysis Pending",
            "divergence_score": 1.0,
            "vision_tags": [],
            "nlp_intent": []
        }
