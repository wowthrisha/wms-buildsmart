import os
import re
import pytesseract
from PIL import Image
import cv2
import numpy as np

def preprocess_image(image_path):
    """Enhance image for better OCR results."""
    # Read image with OpenCV
    img = cv2.imread(image_path)
    if img is None:
        return None
    
    # 1. Grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 2. Noise removal (Gaussian Blur)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    
    # 3. Thresholding (Adaptive)
    # This helps with uneven lighting in photos of blueprints
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                   cv2.THRESH_BINARY, 11, 2)
    
    # Save temp preprocessed image
    pre_path = image_path + ".pre.png"
    cv2.imwrite(pre_path, thresh)
    return pre_path

def extract_text(image_path):
    """Extract text using Tesseract with pre-processing."""
    if not os.path.exists(image_path):
        return ""
    
    processed_path = None
    try:
        # P1: Hardening OCR with OpenCV
        processed_path = preprocess_image(image_path)
        target_path = processed_path if processed_path else image_path
        
        text = pytesseract.image_to_string(Image.open(target_path))
        
        # Cleanup temp file
        if processed_path and os.path.exists(processed_path):
            os.remove(processed_path)
            
        return text.strip()
    except Exception as e:
        print(f"OCR Error: {e}")
        if processed_path and os.path.exists(processed_path):
            try: os.remove(processed_path)
            except: pass
        return ""

def extract_dimensions(image_handle):
    """Production-grade OCR dimension extraction via Tesseract."""
    try:
        text = extract_text(image_handle).lower()
        
        # Scalable regex extraction logic
        def extract(keyword):
            m = re.search(f'{keyword}[\\s]*[:=]?\\s*([\\d\.]+)', text)
            if m:
                return {'value': float(m.group(1)), 'confidence': 0.85}
            return {'value': None, 'confidence': 0.0}

        return {
            'area': extract('area'),
            'frontage': extract('frontage'),
            'depth': extract('depth'),
            'front_setback': extract('front setback'),
            'rear_setback': extract('rear setback'),
            'side_setback': extract('side setback'),
            'height': extract('height'),
            'road_width': extract('road width')
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"OCR Parsing Failed: {e}")
        return {k: {'value': None, 'confidence': 0.0} for k in ['area', 'frontage', 'depth', 'road_width', 'front_setback', 'rear_setback', 'side_setback', 'height']}
