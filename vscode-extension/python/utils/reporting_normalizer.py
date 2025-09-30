import json
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from core.schemas import DetailedImpactReport


def coerce_detailed_report_payload(raw: Any, *, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None

    normalized = normalize_keys(data)
    if not isinstance(normalized, dict):
        return None

    payload: Dict[str, Any] = {}
    payload['path'] = normalized.get('path') or path or ""

    payload['confidence'] = _coerce_confidence(
        normalized.get('confidence') or
        _get_from_dict(normalized.get('analysis'), 'confidence')
    )

    analysis_src = normalized.get('analysis')
    if not isinstance(analysis_src, dict):
        analysis_src = {}

    impact = analysis_src.get('impact')
    if isinstance(impact, bool):
        impact = 'direct' if impact else 'inderect'
    if isinstance(impact, str):
        impact = impact.lower()
    if impact not in {'direct', 'inderect'}:
        direct_flag = analysis_src.get('directly_impacted') or analysis_src.get('is_direct')
        if isinstance(direct_flag, str):
            direct_flag = direct_flag.lower() in {'true', 'yes', 'y', '1'}
        if isinstance(direct_flag, bool):
            impact = 'direct' if direct_flag else 'inderect'
        else:
            impact = 'direct'

    impact_description = (
        analysis_src.get('impact_description') or
        analysis_src.get('details') or
        analysis_src.get('summary') or
        analysis_src.get('explanation') or
        ""
    )

    payload['analysis'] = {
        'impact': impact,
        'impact_description': impact_description
    }

    diagnosis_src = normalized.get('diagnosis')
    if not isinstance(diagnosis_src, dict):
        diagnosis_src = {}

    needs_update = (
        diagnosis_src.get('needs_update') or
        diagnosis_src.get('needs_updates') or
        diagnosis_src.get('requires_update') or
        normalized.get('needs_update')
    )
    if isinstance(needs_update, str):
        needs_update = needs_update.lower() in {'true', 'yes', 'y', '1'}
    if needs_update is None:
        needs_update = True

    update_rationale = (
        diagnosis_src.get('update_rationale') or
        diagnosis_src.get('explanation') or
        diagnosis_src.get('reason') or
        ""
    )

    payload['diagnosis'] = {
        'needs_update': bool(needs_update),
        'update_rationale': update_rationale
    }

    recommendations_src = normalized.get('recommendations')
    actions: List[str]
    implementation = ""

    if isinstance(recommendations_src, dict):
        actions = (
            recommendations_src.get('recommended_actions') or
            recommendations_src.get('actions') or
            recommendations_src.get('steps') or []
        )
        implementation = (
            recommendations_src.get('implementation_approach') or
            recommendations_src.get('implementation') or ""
        )
    elif isinstance(recommendations_src, list):
        actions = recommendations_src
    elif isinstance(recommendations_src, str):
        actions = [recommendations_src]
    else:
        actions = []

    if not isinstance(actions, list):
        actions = [str(actions)]

    recommended_actions = [str(item).strip() for item in actions if str(item).strip()]
    if implementation:
        impl_clean = implementation.strip()
        if impl_clean:
            recommended_actions.append(impl_clean)

    payload['recommendations'] = {
        'recommended_actions': recommended_actions
    }

    related_val = normalized.get('related')
    if isinstance(related_val, str):
        related_val = related_val.lower() in {'true', 'yes', 'y', '1'}
    if related_val is None:
        related_val = payload['diagnosis']['needs_update'] or payload['analysis']['impact'] == 'direct'
    payload['related'] = bool(related_val)

    try:
        report = DetailedImpactReport(**payload)
    except ValidationError:
        return None

    return report.model_dump()


def normalize_keys(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {}
        for key, val in value.items():
            if isinstance(key, str):
                cleaned = key.strip().replace('-', ' ').replace('.', ' ').replace('/', ' ')
                cleaned = "_".join(filter(None, cleaned.lower().split()))
            else:
                cleaned = key
            normalized[cleaned] = normalize_keys(val)
        return normalized
    if isinstance(value, list):
        return [normalize_keys(item) for item in value]
    return value


def _coerce_confidence(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {'high', 'medium', 'low'}:
            return lowered
    return 'medium'


def _get_from_dict(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


def _dedupe_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
