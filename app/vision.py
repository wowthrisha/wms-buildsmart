import torch
from transformers import CLIPProcessor, CLIPModel
from PIL import Image
import threading

class VisionPipeline:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(VisionPipeline, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model_id = "openai/clip-vit-base-patch32"
        self.model = CLIPModel.from_pretrained(self.model_id).to(self.device)
        self.processor = CLIPProcessor.from_pretrained(self.model_id)
        
        # Predefined architectural tags for classification
        self.tags = [
            "modern", "minimalist", "industrial", "traditional", "mediterranean",
            "scandinavian", "art deco", "bohemian", "mid-century modern",
            "interior design", "exterior architecture", "bedroom", "living room",
            "kitchen", "bathroom", "garden", "pool", "natural light", "wooden finish",
            "concrete", "glass walls", "high ceiling"
        ]
        self._initialized = True

    def analyze_image(self, image_path, top_n=5):
        """Extracts top N architectural tags from an image."""
        try:
            image = Image.open(image_path)
            inputs = self.processor(
                text=self.tags, 
                images=image, 
                return_tensors="pt", 
                padding=True
            ).to(self.device)

            outputs = self.model(**inputs)
            logits_per_image = outputs.logits_per_image
            probs = logits_per_image.softmax(dim=1)

            # Get top N results
            values, indices = probs[0].topk(top_n)
            results = []
            for i in range(top_n):
                results.append({
                    "tag": self.tags[indices[i]],
                    "score": float(values[i])
                })
            return results
        except Exception as e:
            import logging
            logging.error(f"Vision Analysis Failed: {e}")
            return []

# Singleton helper
def get_vision_pipeline():
    return VisionPipeline()
