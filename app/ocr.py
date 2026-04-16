"""
OCR engine for plot-analysis documents.

Design goals:
  - single in-memory Tesseract pass via image_to_data()
  - no temporary filesystem writes
  - real Tesseract confidence only
  - FMB-aware zoning so header/footer metadata is not mistaken for geometry
"""

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_FIELDS = [
    'area', 'frontage', 'depth', 'road_width',
    'front_setback', 'rear_setback', 'side_setback',
    'height', 'built_up_area',
]

_EDGE_MARGIN = 0.12
_HEADER_ZONE_RATIO = 0.18
_FOOTER_ZONE_RATIO = 0.88

_NOISE_CONTEXT = frozenset([
    'scale', 'sheet', 'rev', 'revision', 'date', 'north', 'issue',
    'dwg', 'drawing', 'no', 'number', 'drg', 'ref', 'footer',
    'floor', 'storey', 'level', 'detail', 'section', 'fv',
    'title', 'checked', 'drawn', 'approved', 'status', 'district',
    'taluk', 'village', 'survey', 'government', 'tamilnadu', 'tamilnadu',
    'department', 'hect', 'ares', 'area',
])

_CONTEXT_REJECT_LABELS = frozenset([
    'scale', 'area', 'hect', 'ares', 'date', 'issue', 'district',
    'taluk', 'village', 'survey', 'government', 'department', 'north', 'fv',
])

_M_TO_FT = 3.28084
_SQM_TO_SQFT = 10.7639
_LINK_TO_M = 0.201168
_LINK_TO_FT = _LINK_TO_M * _M_TO_FT

_UNIT_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\bsq\.?\s*m\b|\bm[²2]\b|\bsqm\b', re.I), 'sq m'),
    (re.compile(r'\bsq\.?\s*ft\b|\bft[²2]\b|\bsqft\b|\bsq\.?\s*feet\b', re.I), 'sq ft'),
    (re.compile(r"\bfeet\b|\bfoot\b|\bft\b|(?<=\d)'", re.I), 'ft'),
    (re.compile(r'\bmetr(?:es?|ers?)\b|\b(?<![0-9])m(?![0-9a-zA-Z])\b', re.I), 'm'),
]


def _detect_unit(snippet: str) -> Optional[str]:
    for pattern, unit in _UNIT_PATTERNS:
        if pattern.search(snippet):
            return unit
    return None


def _normalize(value: float, unit: Optional[str], field_key: str) -> float:
    if unit is None:
        return value

    if field_key == 'height':
        if unit == 'ft':
            return round(value / _M_TO_FT, 3)
        if unit == 'links':
            return round(value * _LINK_TO_M, 3)
        return value

    if field_key in ('area', 'built_up_area'):
        if unit == 'sq m':
            return round(value * _SQM_TO_SQFT, 2)
        if unit == 'are':
            return round((value * 100.0) * _SQM_TO_SQFT, 2)
        if unit == 'hectare':
            return round((value * 10000.0) * _SQM_TO_SQFT, 2)
        return value

    if unit == 'm':
        return round(value * _M_TO_FT, 2)
    if unit == 'links':
        return round(value * _LINK_TO_FT, 2)
    return value


@dataclass
class Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    conf: float

    @property
    def cx(self):
        return self.x + self.w // 2

    @property
    def cy(self):
        return self.y + self.h // 2

    @property
    def conf_norm(self) -> float:
        return max(0.0, min(1.0, self.conf / 100.0))

    @property
    def num(self) -> Optional[float]:
        cleaned = self.text.replace(',', '').strip("'\"")
        if ':' in cleaned or '/' in cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None


def _zone_limits(img_h: int) -> Tuple[int, int]:
    return int(img_h * _HEADER_ZONE_RATIO), int(img_h * _FOOTER_ZONE_RATIO)


def _word_zone(word: Word, img_h: int) -> str:
    header_max, footer_min = _zone_limits(img_h)
    if word.cy < header_max:
        return 'header'
    if word.cy > footer_min:
        return 'footer'
    return 'drawing'


def _clean_text(text: str) -> str:
    cleaned = re.sub(r'[^0-9A-Za-z:./\- ]+', ' ', text or '')
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


def _preprocess_to_pil(image_path: str):
    try:
        import cv2
        from PIL import Image

        img = cv2.imread(image_path)
        if img is None:
            return None

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        target_max = 2800
        if max(h, w) < target_max:
            scale = target_max / max(h, w)
            gray = cv2.resize(
                gray,
                (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_CUBIC,
            )

        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        thresh = cv2.adaptiveThreshold(
            blurred, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 15, 4,
        )
        return Image.fromarray(thresh)
    except Exception as exc:
        log.warning('_preprocess_to_pil failed: %s', exc)
        return None


def _run_ocr(image_path: str) -> Tuple[List[Word], str, int, int]:
    if not os.path.exists(image_path):
        log.warning('_run_ocr: file not found: %s', image_path)
        return [], '', 0, 0

    try:
        import pytesseract
        from PIL import Image

        pil_img = _preprocess_to_pil(image_path)
        if pil_img is None:
            pil_img = Image.open(image_path).convert('L')

        img_w, img_h = pil_img.size
        data = pytesseract.image_to_data(
            pil_img,
            config='--psm 11 --oem 3',
            output_type=pytesseract.Output.DICT,
        )

        words: List[Word] = []
        for i, txt in enumerate(data['text']):
            txt = txt.strip()
            if not txt:
                continue
            try:
                conf = float(data['conf'][i])
            except (TypeError, ValueError):
                continue
            if conf < 0:
                continue
            words.append(Word(
                text=txt,
                x=data['left'][i],
                y=data['top'][i],
                w=data['width'][i],
                h=data['height'][i],
                conf=conf,
            ))

        raw_text = ' '.join(word.text for word in words)
        return words, raw_text, img_w, img_h
    except Exception as exc:
        log.error('_run_ocr failed: %s', exc, exc_info=True)
        return [], '', 0, 0


def _build_conf_lookup(words: List[Word]) -> Dict[str, List[float]]:
    lookup: Dict[str, List[float]] = {}
    for word in words:
        key = word.text.lower().strip("',\"")
        lookup.setdefault(key, []).append(word.conf_norm)
    return lookup


def _word_conf(num_str: str, lookup: Dict[str, List[float]]) -> float:
    key = num_str.replace(',', '').strip("'\"").lower()
    scores = lookup.get(key, [])
    return round(max(scores), 3) if scores else 0.0


def _match(pattern: str, text: str, flags=re.IGNORECASE):
    match = re.search(pattern, text, flags)
    if match:
        try:
            return match, float(match.group(1).replace(',', ''))
        except (ValueError, IndexError):
            return None, None
    return None, None


def _feet_inches_to_ft(feet, inches=None) -> Optional[float]:
    try:
        val = float(feet)
        if inches:
            val += float(inches) / 12
        return round(val, 2)
    except (ValueError, TypeError):
        return None


def _extract_labelled(text: str, conf_lookup: Dict[str, List[float]]) -> dict:
    t = text.lower()
    found: dict = {}

    area_patterns = [
        r'total\s+plot\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'total\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'plot\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'site\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'area\s*[=:]\s*(\d+(?:[.,]\d+)?)\s*sq',
    ]
    for pat in area_patterns:
        match, val = _match(pat, t)
        if val is None:
            continue
        ctx = t[max(0, match.start() - 10):match.end() + 20]
        unit = _detect_unit(ctx) or 'sq ft'
        conf = _word_conf(match.group(1), conf_lookup)
        found['area'] = (_normalize(val, unit, 'area'), conf, unit, val)
        break

    bua_patterns = [
        r'build\s*[-\s]?up\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'built\s*[-\s]?up\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'builtup\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'buildup\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'plinth\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
        r'floor\s+area\s*[=:]\s*(\d+(?:[.,]\d+)?)',
    ]
    for pat in bua_patterns:
        match, val = _match(pat, t)
        if val is None:
            continue
        ctx = t[max(0, match.start() - 10):match.end() + 20]
        unit = _detect_unit(ctx) or 'sq ft'
        conf = _word_conf(match.group(1), conf_lookup)
        found['built_up_area'] = (_normalize(val, unit, 'built_up_area'), conf, unit, val)
        break

    ann: List[Tuple[float, float]] = []
    for match in re.finditer(r"(\d+)'-(\d+)[\"']\s*\[(\d+\.\d+)m\]", t):
        value_ft = _feet_inches_to_ft(match.group(1), match.group(2))
        if value_ft is not None:
            ann.append((value_ft, _word_conf(match.group(1), conf_lookup)))
    for match in re.finditer(r"(\d+)'\s*\[(\d+\.\d+)m\]", t):
        value_ft = _feet_inches_to_ft(match.group(1))
        if value_ft is not None:
            ann.append((value_ft, _word_conf(match.group(1), conf_lookup)))

    if ann and 'frontage' not in found:
        significant = sorted((item for item in ann if item[0] >= 10), key=lambda item: item[0], reverse=True)
        if len(significant) >= 2:
            found['depth'] = (significant[0][0], significant[0][1], 'ft', significant[0][0])
            found['frontage'] = (significant[1][0], significant[1][1], 'ft', significant[1][0])
        elif significant:
            found['frontage'] = (significant[0][0], significant[0][1], 'ft', significant[0][0])

    label_patterns: Dict[str, list] = {
        'frontage': [
            r'frontage\s*[=:]\s*(\d+(?:\.\d+)?)',
            r'road\s+facing\s+width\s*[=:]\s*(\d+(?:\.\d+)?)',
        ],
        'depth': [
            r'(?<![a-z])depth\s*[=:]\s*(\d+(?:\.\d+)?)',
            r'plot\s+depth\s*[=:]\s*(\d+(?:\.\d+)?)',
        ],
        'road_width': [
            r'road\s+width\s*[=:]\s*(\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)\s*(?:ft|m)?\s*road\b',
        ],
        'front_setback': [
            r'front\s+set\s*[- ]?back\s*[=:]\s*(\d+(?:\.\d+)?)',
            r'front\s*setback\s*[=:]\s*(\d+(?:\.\d+)?)',
        ],
        'rear_setback': [
            r'rear\s+set\s*[- ]?back\s*[=:]\s*(\d+(?:\.\d+)?)',
            r'rear\s*setback\s*[=:]\s*(\d+(?:\.\d+)?)',
        ],
        'side_setback': [
            r'side\s+set\s*[- ]?back\s*[=:]\s*(\d+(?:\.\d+)?)',
            r'side\s*setback\s*[=:]\s*(\d+(?:\.\d+)?)',
        ],
        'height': [
            r'building\s+height\s*[=:]\s*(\d+(?:\.\d+)?)',
            r'height\s*[=:]\s*(\d+(?:\.\d+)?)\s*m\b',
        ],
    }
    for field_key, patterns in label_patterns.items():
        if field_key in found:
            continue
        for pat in patterns:
            match, val = _match(pat, t)
            if val is None:
                continue
            ctx = t[max(0, match.start() - 5):match.end() + 20]
            unit = _detect_unit(ctx)
            conf = _word_conf(match.group(1), conf_lookup)
            found[field_key] = (_normalize(val, unit, field_key), conf, unit, val)
            break

    return found


def _coerce_ocr_digits(text: str) -> str:
    return (text or '').lower().replace('o', '0').replace('q', '0')


def _extract_fmb_metadata(words: List[Word], conf_lookup: Dict[str, List[float]], img_h: int):
    header_words = [word for word in words if _word_zone(word, img_h) == 'header']
    text = _clean_text(' '.join(word.text for word in sorted(header_words, key=lambda item: (item.cy, item.cx)))).lower()

    found = {}
    metadata = {}

    area_match = None
    hectares = 0
    ares = None
    area_patterns = [
        re.compile(r'(?:area\s*[:>\-]?\s*)?hect\s*([0-9oq]+)\s*ares?\s*([0-9]+(?:\.[0-9]+)?)', re.I),
        re.compile(r'([0-9oq]+)\s*hect\s*ares?\s*([0-9]+(?:\.[0-9]+)?)', re.I),
        re.compile(r'hect\s*ares?\s*([0-9]+(?:\.[0-9]+)?)', re.I),
    ]
    for pattern in area_patterns:
        area_match = pattern.search(text)
        if area_match:
            if pattern.groups == 2:
                hectares = int(_coerce_ocr_digits(area_match.group(1)) or '0')
                ares = float(area_match.group(2))
            else:
                hectares = 0
                ares = float(area_match.group(1))
            break

    if area_match and ares is not None:
        conf = max(_word_conf(str(ares), conf_lookup), _word_conf(str(hectares), conf_lookup))
        found['area'] = (_normalize(ares, 'are', 'area'), conf, 'are', ares)
        metadata['area_original_text'] = area_match.group(0).strip()
        metadata['plot_area_sqm'] = round((hectares * 10000.0) + (ares * 100.0), 2)
    else:
        sqm_match = re.search(r'(?:area\s*[:>\-]?\s*)?([0-9]+(?:\.[0-9]+)?)\s*(?:sq\s*m|sqm)', text, re.I)
        if sqm_match:
            sqm_val = float(sqm_match.group(1))
            found['area'] = (_normalize(sqm_val, 'sq m', 'area'), _word_conf(sqm_match.group(1), conf_lookup), 'sq m', sqm_val)
            metadata['plot_area_sqm'] = sqm_val

    survey_match = re.search(r'survey\s*no\s*[:>\-]?\s*([0-9/]+[a-z]?)', text, re.I)
    if survey_match:
        metadata['survey_number'] = survey_match.group(1).upper()

    scale_match = re.search(r's(?:c|e)ale\s*[:>\-]?\s*1\s*[:/\-]\s*(\d{2,4})', text, re.I)
    if scale_match:
        metadata['scale_denominator'] = int(scale_match.group(1))

    return found, metadata


def _nearby_words(target: Word, words: List[Word], x_pad=120, y_pad=90) -> List[Word]:
    nearby = []
    for word in words:
        if word is target:
            continue
        if abs(word.cx - target.cx) <= x_pad and abs(word.cy - target.cy) <= y_pad:
            nearby.append(word)
    return nearby


def _is_likely_scale_ratio(word: Word, words: List[Word]) -> bool:
    cleaned = word.text.lower().replace(',', '').strip()
    if ':' in cleaned:
        return True

    nearby_text = ' '.join(item.text.lower() for item in _nearby_words(word, words))
    if 'scale' not in nearby_text:
        return False

    if re.match(r'^1[.:/\-]?\d{2,4}$', cleaned):
        return True
    if re.match(r'^1\.\d{3,4}$', cleaned):
        return True
    return False


def classify_edge_position(cx: int, cy: int, img_w: int, img_h: int) -> str:
    header_max, footer_min = _zone_limits(img_h)
    drawing_height = max(1, footer_min - header_max)
    edge_threshold_x = img_w * _EDGE_MARGIN
    edge_threshold_y = drawing_height * _EDGE_MARGIN

    if cy < header_max + edge_threshold_y:
        return 'top_edge'
    if cy > footer_min - edge_threshold_y:
        return 'bottom_edge'
    if cx < edge_threshold_x:
        return 'left_edge'
    if cx > img_w - edge_threshold_x:
        return 'right_edge'
    return 'interior'


def _reject_for_context(word: Word, words: List[Word]) -> bool:
    nearby = _nearby_words(word, words, x_pad=140, y_pad=90)
    for item in nearby:
        token = item.text.lower().strip('.:,;()[]{}')
        if token in _CONTEXT_REJECT_LABELS or token in _NOISE_CONTEXT:
            return True
    return False


def _extract_positional(words: List[Word], img_w: int, img_h: int, assume_links: bool = False) -> dict:
    if not words or img_w <= 0 or img_h <= 0:
        return {}

    candidates = []
    for word in words:
        if _word_zone(word, img_h) != 'drawing':
            continue
        num = word.num
        if num is None or num < 1 or num > 5000:
            continue
        if word.conf < 35:
            continue
        if _is_likely_scale_ratio(word, words):
            continue
        if _reject_for_context(word, words):
            continue

        edge = classify_edge_position(word.cx, word.cy, img_w, img_h)
        if edge == 'interior':
            continue

        candidates.append({
            'value': num,
            'conf': word.conf_norm,
            'edge': edge,
        })

    found: dict = {}
    top_edge = [item for item in candidates if item['edge'] == 'top_edge']
    left_right = [item for item in candidates if item['edge'] in ('left_edge', 'right_edge')]

    if top_edge:
        chosen = max(top_edge, key=lambda item: (item['value'], item['conf']))
        unit = 'links' if assume_links else None
        found['frontage'] = (
            _normalize(chosen['value'], unit, 'frontage') if unit else chosen['value'],
            round(chosen['conf'], 3),
            unit,
            chosen['value'],
        )

    if left_right:
        chosen = max(left_right, key=lambda item: (item['value'], item['conf']))
        unit = 'links' if assume_links else None
        found['depth'] = (
            _normalize(chosen['value'], unit, 'depth') if unit else chosen['value'],
            round(chosen['conf'], 3),
            unit,
            chosen['value'],
        )

    return found


def extract_dimensions(image_path: str, doc_type: Optional[str] = None) -> dict:
    result = {
        key: {'value': None, 'confidence': 0.0, 'unit_original': None, 'value_original': None}
        for key in _FIELDS
    }
    result['_raw_text'] = ''
    result['_metadata'] = {}

    if not os.path.exists(image_path):
        log.warning('extract_dimensions: file not found: %s', image_path)
        return result

    start = time.perf_counter()
    try:
        words, raw_text, img_w, img_h = _run_ocr(image_path)
        result['_raw_text'] = raw_text
        conf_lookup = _build_conf_lookup(words)
        labelled = _extract_labelled(raw_text, conf_lookup)

        structured = {}
        metadata = {}
        assume_links = False
        if (doc_type or '').upper() == 'FMB':
            structured, metadata = _extract_fmb_metadata(words, conf_lookup, img_h)
            assume_links = True

        positional = _extract_positional(words, img_w, img_h, assume_links=assume_links)
        merged = {**positional, **labelled, **structured}

        for field_key, payload in merged.items():
            if field_key not in _FIELDS:
                continue
            val, conf, unit_original, value_original = payload
            if val is None:
                continue
            result[field_key] = {
                'value': val,
                'confidence': round(conf, 3),
                'unit_original': unit_original,
                'value_original': value_original,
            }

        result['_metadata'] = metadata
        duration = time.perf_counter() - start
        extracted = {k: v for k, v in result.items() if k in _FIELDS and v['value'] is not None}
        log.info(
            'extract_dimensions(%s): %d/%d fields in %.2fs',
            doc_type or 'generic',
            len(extracted),
            len(_FIELDS),
            duration,
        )
    except Exception as exc:
        log.error('extract_dimensions crashed: %s', exc, exc_info=True)

    return result
