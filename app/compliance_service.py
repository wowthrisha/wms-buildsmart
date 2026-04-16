from __future__ import annotations

from app import db
from app.compliance_catalog import COMPLIANCE_DEFINITIONS, COMPLIANCE_TREE, default_compliance_payload, definition_for
from app.models import ComplianceItem
from app.time_utils import utc_now


def ensure_project_compliance_items(project_id: int) -> list[ComplianceItem]:
    existing = {
        (item.doc_type or item.item or '').strip().lower(): item
        for item in ComplianceItem.query.filter_by(project_id=project_id).all()
    }
    created = []
    for payload in default_compliance_payload():
        key = payload['doc_type']
        if key in existing:
            item = existing[key]
            changed = False
            if not item.doc_type:
                item.doc_type = key
                changed = True
            if not item.item:
                item.item = key
                changed = True
            if not item.label:
                item.label = payload['label']
                changed = True
            if not item.category:
                item.category = payload['category']
                changed = True
            if item.required is None:
                item.required = payload['required']
                changed = True
            if not item.status:
                item.status = 'missing' if payload['required'] else 'optional'
                changed = True
            elif item.required is False and item.status == 'missing' and not item.document_id:
                item.status = 'optional'
                changed = True
            if not item.description:
                item.description = payload['description']
                changed = True
            if changed:
                item.updated_at = utc_now()
            continue

        item = ComplianceItem(
            project_id=project_id,
            item=key,
            doc_type=key,
            label=payload['label'],
            category=payload['category'],
            required=payload['required'],
            status='missing' if payload['required'] else 'optional',
            description=payload['description'],
            updated_at=utc_now(),
        )
        db.session.add(item)
        created.append(item)

    if created:
        db.session.flush()

    return (
        ComplianceItem.query
        .filter_by(project_id=project_id)
        .order_by(ComplianceItem.required.desc(), ComplianceItem.label.asc())
        .all()
    )


def compliance_summary(project) -> dict:
    items = ensure_project_compliance_items(project.id)
    total = len(items)
    uploaded = sum(1 for item in items if item.status in ('uploaded', 'verified'))
    verified = sum(1 for item in items if item.status == 'verified')
    return {
        'total': total,
        'uploaded': uploaded,
        'verified': verified,
        'missing': sum(1 for item in items if item.status == 'missing'),
    }


def coverage_by_category(project) -> list[dict]:
    """Return per-category upload coverage for the progress bar."""
    items = {item.doc_type: item for item in ensure_project_compliance_items(project.id)}
    result = []
    for group_key, doc_types in COMPLIANCE_TREE.items():
        total = len(doc_types)
        uploaded = sum(
            1 for dt in doc_types
            if (items.get(dt) and items[dt].status in ('uploaded', 'verified'))
        )
        result.append({
            'key': group_key,
            'label': group_key.replace('_', ' ').title(),
            'total': total,
            'uploaded': uploaded,
            'pct': round(uploaded / total * 100) if total else 0,
        })
    return result


def build_compliance_tree(project) -> dict:
    items = {item.doc_type: item for item in ensure_project_compliance_items(project.id)}
    groups = []
    for group_key, doc_types in COMPLIANCE_TREE.items():
        children = []
        for doc_type in doc_types:
            item = items.get(doc_type)
            definition = definition_for(doc_type)
            extracted_fields = {}
            if item and item.document and item.document.extracted_data:
                try:
                    import json
                    extracted_fields = json.loads(item.document.extracted_data)
                except Exception:
                    extracted_fields = {}
            children.append({
                'doc_type': doc_type,
                'label': item.label if item else definition['label'],
                'status': (item.status if item else ('missing' if definition['required'] else 'optional')),
                'required': item.required if item else definition['required'],
                'description': (item.description if item else definition['description']) or '',
                'importance': definition['importance'],
                'what_it_is': definition.get('what_it_is', ''),
                'why_needed': definition.get('why_needed', ''),
                'document_id': item.document_id if item else None,
                'item_id': item.id if item else None,
                'extracted_fields': extracted_fields,
            })

        groups.append({
            'key': group_key,
            'label': group_key.replace('_', ' ').title(),
            'children': children,
        })

    return {'groups': groups}
