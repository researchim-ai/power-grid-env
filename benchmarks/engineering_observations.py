"""Versioned, read-only compact public PF views; no evaluator or private labels."""
import copy
import json


def is_pf_report(result):
    return isinstance(result,dict) and result.get('profile')=='passive_power_flow' and type(result.get('converged')) is bool and 'status' in result


def compact_pf(result):
    if not is_pf_report(result):
        return copy.deepcopy(result)
    # Keep criteria-bearing values and passports verbatim. Rendering/duplicate SI
    # voltage fields and law-check arithmetic are omitted by this explicit view.
    nodes=[]
    for node in result['nodes']:
        nodes.append(dict(id=node['id'],energized=node['energized'],
            nominal_ll_v=node['nominal_ll_v'],phases={phase:{k:values[k] for k in ('voltage_pu','energized')}
                for phase,values in node['phases'].items()}))
    branches=[]
    for branch in result['branches']:
        b={k:copy.deepcopy(branch[k]) for k in ('id','kind','from_node','to_node','energized','closed','loading_pct','loss_kw','passport')}
        for terminal in ('from_terminal','to_terminal'):
            b[terminal]=None if branch[terminal] is None else {k:copy.deepcopy(branch[terminal][k]) for k in ('p_kw','q_kvar','current_a','phase_current_a')}
        branches.append(b)
    return dict(profile=result['profile'],status=result['status'],converged=result['converged'],
        nodes=nodes,branches=branches,source=copy.deepcopy(result['source']),
        warnings=copy.deepcopy(result.get('warnings',[])),
        loads=[{k:copy.deepcopy(load[k]) for k in ('id','node_id','passport','actual')} for load in result['loads']],
        public_view=dict(schema='power-grid-env.compact-pf-observation/v1',
            omitted=['layout_coordinates','voltage_v','phase_angles','opaque_island_ids','load_law_residuals'],
            scope='Bounded authored passive PF task criteria only;not a general phasor or protection observation'))


def compact_messages(messages):
    projected=copy.deepcopy(messages)
    for message in projected:
        if message.get('role')!='user':continue
        try:value=json.loads(message['content'])
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and is_pf_report(value.get('result')):
            value['result']=compact_pf(value['result'])
        message['content']=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    return projected


def referenced_messages(messages):
    """Exact-repeat references keep every distinct public PF observation once."""
    import hashlib
    compact=compact_messages(messages)
    seen={}
    for i,(original,message) in enumerate(zip(messages,compact)):
        if original.get('role')!='user':continue
        try:value=json.loads(original['content'])
        except (ValueError,TypeError):continue
        if not isinstance(value,dict) or not is_pf_report(value.get('result')):continue
        digest=hashlib.sha256(json.dumps(value['result'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if digest in seen:
            reduced=json.loads(message['content'])
            reduced['result']=dict(same_public_pf_result_as_message=seen[digest],original_result_sha256=digest)
            message['content']=json.dumps(reduced,ensure_ascii=False,separators=(',',':'))
        else:seen[digest]=i
    return compact
