"""Voice-only projection; never infer tool access from an upstream tool list."""
import json

ALLOWED = {
    'ops-readonly': frozenset({'ops_decisions_first_page', 'ops_actions_first_page'}),
    'ops-ai': frozenset({'ops_custom_plan_status'}),
    'ops-actions': frozenset({'ops_decision_adopt', 'ops_decision_reject'}),
    'ops-collection': frozenset({'ops_collection_trigger'}),
}

def permitted(domain, tool):
    return tool in ALLOWED.get(domain, ())

def compact(tool, value):
    def dump(v):
        return json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    if not isinstance(value, dict) or value.get('code') != 0:
        return dump({'ok': False, 'error': 'upstream_business_rejection'})
    data = value.get('data')
    if tool in ('ops_decision_adopt', 'ops_decision_reject', 'ops_collection_trigger'):
        if not isinstance(data, dict) or not isinstance(data.get('status'), str):
            return dump({'ok': False, 'error': 'unknown_operation_receipt', 'retry': False})
        fields = ('batchNo', 'connectorCode', 'syncType', 'status') if tool == 'ops_collection_trigger' else ('id', 'decisionNo', 'status', 'adoptedPlanId')
        output = {'ok': data['status'] not in ('FAILED', 'UNKNOWN'), 'data': {k: str(data[k])[:80] for k in fields if data.get(k) is not None}, 'retry': False}
    elif tool == 'ops_custom_plan_status':
        if not isinstance(data, dict) or not isinstance(data.get('status'), str):
            return dump({'ok': False, 'error': 'invalid_upstream_receipt'})
        fields = ('status', 'generationId', 'failureKind')
        output = {'ok': True, 'data': {k: str(data[k])[:80] for k in fields if isinstance(data, dict) and k in data}}
    else:
        if not isinstance(data, dict) or not isinstance(data.get('records'), list):
            return dump({'ok': False, 'error': 'invalid_upstream_shape'})
        fields = ('decisionNo', 'title', 'status', 'ownerName', 'deadline') if tool == 'ops_decisions_first_page' else ('decisionId', 'actionType', 'fromStatus', 'toStatus', 'createTime', 'pushStatus')
        output = {'ok': True, 'total': str(data.get('total', 'unknown')) if isinstance(data.get('total'), str) else data.get('total', 'unknown'), 'items': [], 'returned': 0, 'truncated': False}
        for row in data['records']:
            if not isinstance(row, dict):
                return dump({'ok': False, 'error': 'invalid_upstream_record'})
            item = {k: str(row[k])[:120] for k in fields if row.get(k) is not None}
            shortened = any(len(str(row[k])) > 120 for k in fields if row.get(k) is not None)
            candidate = {**output, 'items': output['items'] + [item], 'returned': len(output['items']) + 1, 'truncated': output['truncated'] or shortened}
            if len(dump(candidate).encode('utf-8')) > 1000:
                break
            output = candidate
        output['truncated'] = output['truncated'] or output['returned'] < len(data['records'])
    text = dump(output)
    return text if len(text.encode('utf-8')) <= 1024 else dump({'ok': False, 'error': 'result_too_large'})
