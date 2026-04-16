"""
Compatibility helpers for code paths that still import app.plot_rules.

All rule computation delegates to the single source of truth in tn_engine.py.
"""

from app.tn_engine import get_engine

ENGINE = get_engine()

_LEGACY_LABELS = {
    'area':          'Plot Area',
    'frontage':      'Frontage',
    'depth':         'Depth',
    'front_setback': 'Front Setback',
    'rear_setback':  'Rear Setback',
    'side_setback':  'Side Setback',
    'height':        'Height',
    'built_up_area': 'Built-up Area / FSI',
    'road_width':    'Road Width',
}

# Direct legacy rule thresholds (in the same units as the input field_map)
_MIN_THRESHOLDS = {
    'area':          600.0,   # sq ft
    'frontage':       20.0,   # ft
    'road_width':     12.0,   # ft
    'depth':          30.0,   # ft
    'front_setback':   3.0,   # ft
    'rear_setback':    1.0,   # ft
    'side_setback':    1.0,   # ft
}
_MAX_THRESHOLDS = {
    'height':  8.5,   # metres
}
_FSI_MAX = 2.0   # built_up_area / area


def _legacy_to_engine_fields(field_map: dict) -> dict:
    """Convert legacy ft/sqft field names → engine SI field names."""
    area_sqft = field_map.get('area')
    built_up_sqft = field_map.get('built_up_area')

    fields = {
        'building_type': 'residential',
        'location_type': 'corporation',
    }

    if area_sqft not in (None, ''):
        fields['plot_area'] = ENGINE.sq_ft_to_sq_m(float(area_sqft))
    if field_map.get('road_width') not in (None, ''):
        fields['road_width'] = ENGINE.ft_to_m(float(field_map['road_width']))
    if field_map.get('frontage') not in (None, ''):
        fields['frontage'] = ENGINE.ft_to_m(float(field_map['frontage']))
    if field_map.get('height') not in (None, ''):
        fields['height_m'] = float(field_map['height'])
    if field_map.get('front_setback') not in (None, ''):
        fields['front_setback_m'] = ENGINE.ft_to_m(float(field_map['front_setback']))
    if field_map.get('rear_setback') not in (None, ''):
        fields['rear_setback_m'] = ENGINE.ft_to_m(float(field_map['rear_setback']))
    if field_map.get('side_setback') not in (None, ''):
        fields['side_setback_m'] = ENGINE.ft_to_m(float(field_map['side_setback']))
    if area_sqft not in (None, '') and built_up_sqft not in (None, '') and float(area_sqft):
        fields['fsi_proposed'] = round(float(built_up_sqft) / float(area_sqft), 4)

    return fields
def score_to_status(score, trust_level=None):
    if trust_level == 'INSUFFICIENT':
        return 'not_available'
    if score is None:
        return 'not_available'
    if score >= 85:
        return 'pass'
    if score >= 60:
        return 'warning'
    return 'fail'


def score_to_label(score, trust_level=None):
    return {
        'pass': 'Compliant',
        'warning': 'Needs Review',
        'fail': 'Non-Compliant',
        'not_available': 'Incomplete',
    }.get(score_to_status(score, trust_level=trust_level), 'Incomplete')
