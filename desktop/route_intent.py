"""Versioned route-intent DTOs and explicit new-snapshot resolution.

Old extensions stay readable. A normal read never recalculates a saved path.
Canonical repository adapters translate source aliases to stable domain IDs.
"""
from copy import deepcopy
import hashlib

RESOLUTION_KEY = 'railscope.org/line-resolution'
INTENT_SCHEMA = 'railscope.route-intent.v1'
RESOLVED_SCHEMA = 'railscope.resolved-corridor.v1'


def promote_route(route, *, refresh=False):
    result = deepcopy(route)
    resolution = result.get('extensions', {}).get(RESOLUTION_KEY, {})
    selection = resolution.get('selection', {})
    requested = selection.get('requested_sequence', result.get('sequence', []))
    if refresh or 'route_intent' not in result:
        result['route_intent'] = {
            'schema': INTENT_SCHEMA,
            'id': result.get('route_intent', {}).get('id') or 'RI-' + hashlib.sha256(str(result['id']).encode()).hexdigest()[:20],
            'sequence': deepcopy(requested),
            'resolution_policy': resolution.get('policy', 'strict'),
            'source': resolution.get('source', 'legacy_migration'),
            'snapshot_id': resolution.get('snapshot'),
        }
    if refresh or 'resolved_corridor' not in result:
        result['resolved_corridor'] = {
            'schema': RESOLVED_SCHEMA,
            'sequence': deepcopy(result.get('sequence', selection.get('resolved_sequence', []))),
            'path': deepcopy(result.get('path', selection.get('path', []))),
            'snapshot_id': resolution.get('snapshot'),
            'verification_status': resolution.get('verification_status', 'unverified'),
        }
    validate_route_intent(result)
    return result


def validate_route_intent(route):
    intent = route.get('route_intent')
    resolved = route.get('resolved_corridor')
    if intent is not None:
        if not isinstance(intent, dict) or intent.get('schema') != INTENT_SCHEMA:
            raise ValueError('RouteIntent 版本无效')
        sequence = intent.get('sequence')
        if not isinstance(sequence, list) or sequence and (len(sequence) < 3 or len(sequence) % 2 != 1):
            raise ValueError('RouteIntent 必须为端点—线路—端点序列')
        for index, step in enumerate(sequence):
            expected, key = ('line','line_id') if index % 2 else ('endpoint','node_id')
            if not isinstance(step, dict) or step.get('kind') != expected or step.get(key) is None:
                raise ValueError('RouteIntent 步骤无效')
        if intent.get('resolution_policy') not in ('auto','strict','mainline'):
            raise ValueError('RouteIntent 解析策略无效')
    if resolved is not None:
        if not isinstance(resolved, dict) or resolved.get('schema') != RESOLVED_SCHEMA:
            raise ValueError('ResolvedCorridor 版本无效')
        if 'path' in route and resolved.get('path') != route['path']:
            raise ValueError('ResolvedCorridor 与保存的物理路径冲突，未修改原通道')
        if 'sequence' in route and resolved.get('sequence') != route['sequence']:
            raise ValueError('ResolvedCorridor 与保存的端点序列冲突')


def reresolve_route(route, library, snapshot_id=None):
    """Return a candidate; callers validate dependent trains before saving it."""
    candidate = promote_route(route)
    intent = candidate['route_intent']
    if not intent['sequence']:
        raise ValueError('旧通道没有业务意图，需先补充端点和线路')
    requested = deepcopy(intent['sequence'])
    policy = intent['resolution_policy']
    if hasattr(library, 'resolve_with_sequence'):
        sequence, path = library.resolve_with_sequence(requested, policy, selection=None)
    else:
        sequence = library.normalize_sequence(requested)
        path = library.resolve(sequence, policy, selection=None)
    extension = candidate.setdefault('extensions', {})
    extension[RESOLUTION_KEY] = {'policy': policy, 'source': 'explicit_snapshot_reresolution',
        'snapshot': snapshot_id, 'version': 4, 'confidence': None,
        'verification_status': 'automatic_reference_not_dispatch_verified',
        'selection': {'requested_sequence': requested, 'resolved_sequence': deepcopy(sequence), 'path': deepcopy(path)}}
    candidate.update(sequence=sequence, path=path)
    return promote_route(candidate, refresh=True)
