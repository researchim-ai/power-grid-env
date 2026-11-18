"""Public,tri-state measurement assessment for the balanced card profile.

This checks displayed solved measurements and public mutation budgets. It does
not certify full passport equality,structural completeness or a feasible design.
"""
import copy,json,math


def number(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('finite public numeric field required:'+name)
    return float(value)


def assess(card):
    fresh=card['has_fresh_measurements'];study=card['study'];goal=card['goal']
    if type(fresh) is not bool:raise ValueError('public freshness must be boolean')
    if fresh and not isinstance(study,dict):raise ValueError('fresh study evidence missing')
    if not fresh and study is not None:raise ValueError('stale card cannot carry current-study evidence')
    budget=True
    for limit,used in [('maximum_catalog_edits','catalog_edits_used'),('maximum_changed_routes','changed_routes')]:
        if limit not in goal:continue
        n=goal[limit]
        if type(n) is not int or n<0:raise ValueError('nonnegative integer budget required')
        current=card[used]
        if used=='changed_routes':
            if not isinstance(current,list) or any(not isinstance(x,str) for x in current) or len(set(current))!=len(current):raise ValueError('distinct public changed-route handles required')
            current=len(current)
        if type(current) is not int or current<0:raise ValueError('nonnegative mutation counter required')
        budget=budget and current<=n
    ready=fresh and study.get('converged') is True
    result=dict(study_ready=ready,measurement_feasible=None,overloaded_branch_count=None,undervoltage_node_count=None,deenergized_node_count=None,equipment_budget_ok=budget)
    if not ready:return result
    minimum=number(goal['minimum_voltage_pu'],'minimum_voltage_pu');maximum=number(goal['maximum_branch_loading_pct'],'maximum_branch_loading_pct')
    if minimum<=0 or maximum<=0:raise ValueError('positive measurement limits required')
    if not study['nodes']:raise ValueError('completed study has no node observations')
    deenergized=0;undervoltage=0;overloaded=0
    for n in study['nodes']:
        if type(n['energized']) is not bool:raise ValueError('energization must be boolean')
        voltage=number(n['voltage_pu_min'],'voltage_pu_min')
        if not n['energized']:deenergized+=1
        elif voltage<minimum:undervoltage+=1
    for b in study['branches']:
        loading=b['loading_pct']
        if loading is not None and number(loading,'loading_pct')>maximum:overloaded+=1
    result.update(measurement_feasible=not (deenergized or undervoltage or overloaded),overloaded_branch_count=overloaded,undervoltage_node_count=undervoltage,deenergized_node_count=deenergized)
    return result


def messages(card):
    public=copy.deepcopy(card);public.pop('tools')
    instruction=('MEASUREMENT-ASSESSMENT/V1. Read only the public goal and observations. Return JSON fields '
        'study_ready,measurement_feasible,overloaded_branch_count,undervoltage_node_count,deenergized_node_count,equipment_budget_ok. '
        'Without a fresh converged study,the four measurement feasibility/count fields are null. '
        'Overload means loading strictly above its limit;undervoltage counts energized nodes strictly below the voltage limit. '
        'Equipment budgets apply independently of measurement feasibility. This is not complete design certification.')
    return [dict(role='system',content=instruction),dict(role='user',content=json.dumps(public,separators=(',',':')))]
