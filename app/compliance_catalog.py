import re


COMPLIANCE_TREE = {
    'ownership': ['patta', 'ec', 'sale_deed'],
    'dimensions': ['fmb', 'site_measurement'],
    'road': ['road_width', 'authority'],
    'approval': ['layout_approval'],
}


COMPLIANCE_DEFINITIONS = {
    'patta': {
        'label': 'Patta',
        'category': 'ownership',
        'required': True,
        'description': 'Primary ownership record used to establish legal title.',
        'importance': 'Critical',
        'what_it_is': 'Revenue document issued by the Tahsildar proving land ownership.',
        'why_needed': 'Mandatory proof of title for any building permit or approval application.',
    },
    'ec': {
        'label': 'Encumbrance Certificate',
        'category': 'ownership',
        'required': True,
        'description': 'Checks registered encumbrances affecting the site.',
        'importance': 'Critical',
        'what_it_is': 'Certificate from Sub-Registrar listing all registered transactions on the plot.',
        'why_needed': 'Confirms the land is free of mortgages, liens, or legal disputes.',
    },
    'sale_deed': {
        'label': 'Sale Deed',
        'category': 'ownership',
        'required': True,
        'description': 'Transfer deed supporting ownership continuity.',
        'importance': 'Critical',
        'what_it_is': 'Registered legal document recording the transfer of property ownership.',
        'why_needed': 'Establishes the chain of title and supports ownership continuity.',
    },
    'fmb': {
        'label': 'FMB Sketch',
        'category': 'dimensions',
        'required': True,
        'description': 'Field Measurement Book extract used for site dimensions.',
        'importance': 'Critical',
        'what_it_is': 'Survey sketch from the Revenue Department showing plot boundaries and dimensions.',
        'why_needed': 'Used to verify plot area, shape, and setback calculations against regulations.',
    },
    'site_measurement': {
        'label': 'Site Measurement',
        'category': 'dimensions',
        'required': False,
        'description': 'Latest on-site measurement or survey confirmation.',
        'importance': 'Helpful',
        'what_it_is': 'Physical measurement report by a licensed surveyor confirming current site dimensions.',
        'why_needed': 'Validates FMB data against actual ground conditions; required if discrepancies exist.',
    },
    'road_width': {
        'label': 'Road Width Proof',
        'category': 'road',
        'required': True,
        'description': 'Documentary proof of abutting road width for access checks.',
        'importance': 'Required',
        'what_it_is': 'Certificate or municipal record confirming the width of the road abutting the plot.',
        'why_needed': 'Road width determines permissible building height and setback under TNPCR norms.',
    },
    'authority': {
        'label': 'Authority Record',
        'category': 'road',
        'required': False,
        'description': 'Relevant local authority or jurisdiction support document.',
        'importance': 'Helpful',
        'what_it_is': 'Document identifying the local body (CMDA, DTCP, Panchayat) with jurisdiction over the plot.',
        'why_needed': 'Determines which approval authority applies and the applicable development rules.',
    },
    'layout_approval': {
        'label': 'Layout Approval',
        'category': 'approval',
        'required': True,
        'description': 'Approved layout or subdivision approval reference.',
        'importance': 'Critical',
        'what_it_is': 'Approval letter from CMDA/DTCP sanctioning the layout plan for the subdivision.',
        'why_needed': 'Mandatory for any construction; confirms the plot is in an approved layout.',
    },
}


DEFAULT_COMPLIANCE_ORDER = tuple(
    doc_type
    for group in COMPLIANCE_TREE.values()
    for doc_type in group
)


def normalize_doc_type(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if not raw:
        return ''
    return re.sub(r'[^a-z0-9]+', '_', raw).strip('_')


def definition_for(doc_type: str) -> dict:
    normalized = normalize_doc_type(doc_type)
    definition = COMPLIANCE_DEFINITIONS.get(normalized, {})
    return {
        'doc_type': normalized,
        'label': definition.get('label') or normalized.replace('_', ' ').title(),
        'category': definition.get('category', 'custom'),
        'required': bool(definition.get('required', False)),
        'description': definition.get('description', ''),
        'importance': definition.get('importance', 'Optional'),
        'what_it_is': definition.get('what_it_is', ''),
        'why_needed': definition.get('why_needed', ''),
    }


def default_compliance_payload():
    return [definition_for(doc_type) for doc_type in DEFAULT_COMPLIANCE_ORDER]
