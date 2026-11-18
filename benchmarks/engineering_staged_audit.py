"""Mechanical replay of generated two-stage decisions and numerical studies.

Diagnostic failures are retained. Replaying an unfinished/failed episode never
promotes it to success. Numerical reference covers the balanced constant-Z scope.
"""
from pathlib import Path
from benchmarks.engineering_staged_agent import decide,public_feedback
from benchmarks.engineering_replacement_challenges import ReplacementEpisode
from benchmarks.engineering_reference import compare_reference
from benchmarks.engineering_skill_tasks import score
from benchmarks.engineering_factory import write_json
from power_grid_env.study_artifacts import verify,read_json,seal


def replay(case,record,processor=None,require_native=True):
    if record.get('profile')!='staged-operation-args/v1' or record['task_id']!=case['id'] or record['base_project_id']!=case['base_project_id'] or record['family_sha256']!=case['family_sha256'] or record['split']!=case['split']:
        raise ValueError('staged episode attribution differs')
    e=ReplacementEpisode(case);feedback=None;references=[];completions=0;protocol_errors=0;requests=[];accepted_edits=0;filled_slots=0
    if not isinstance(record['events'],list) or not 0<len(record['events'])<=24:raise ValueError('complete bounded staged trace required')
    for event in record['events']:
        if e.engine.state['finished'] or e.engine.state['calls']>=24:raise ValueError('events after terminal/budget')
        card,_=e.observe()
        if card!=event['card'] or feedback!=event['previous_feedback']:raise ValueError('staged public card/feedback does not replay')
        saved=event['decision'];cursor=[0]
        def generated(q):
            i=cursor[0];cursor[0]+=1
            if i>=len(saved['requests']) or q!=saved['requests'][i]:raise ValueError('candidate request differs from public replay')
            if i<len(saved['completions']):return saved['completions'][i]
            error=saved.get('error')
            if not isinstance(error,str) or ':' not in error:raise ValueError('missing candidate completion')
            kind,detail=error.split(':',1);classes={'ValueError':ValueError,'RuntimeError':RuntimeError,'TypeError':TypeError}
            if kind not in classes:raise ValueError('unreplayable native exception type')
            raise classes[kind](detail)
        decision=decide(card,generated,feedback)
        if decision!=saved or cursor[0]!=len(saved['requests']):raise ValueError('generated completions/actions do not replay')
        protocol_errors+=decision['error'] is not None;completions+=len(decision['completions'])
        requests.extend(decision['requests'][:len(decision['completions'])])
        action=decision['action']
        if action is None:reply=e.engine.apply('__invalid_action__',{})
        else:
            spec=next(t['function']['parameters'] for t in card['tools'] if t['function']['name']==action['name'])
            filled_slots+=sum(k in action['arguments'] and action['arguments'][k] not in (None,'') for k in spec.get('required',[]))
            reply,_=e.apply(action)
            if reply.get('ok') is True and action['name'] in ('set_line_catalog','remove_line','add_line','add_load'):accepted_edits+=1
            if reply.get('ok') is True and action['name']=='simulate' and e.engine.state['last_result'].get('converged') is True:
                references.append(compare_reference(e.engine.state['project'],e.engine.state['last_result']))
        if reply!=event['response']:raise ValueError('staged tool response differs')
        feedback=public_feedback(reply,action)
    if not e.engine.state['finished']:e.engine.apply('__invalid_action__',{})
    if (e.engine.state['calls']!=record['calls'] or e.engine.state['terminal']!=record['terminal'] or
        (e.engine.state['terminal']['success'] is True)!=record['success'] or completions!=record['candidate_completions'] or protocol_errors!=record['protocol_errors']):
        raise ValueError('staged terminal/counters differ')
    if require_native:
        native=record.get('native_calls')
        if not isinstance(native,list) or len(native)!=completions:raise ValueError('native completion denominator differs')
        for meta,q in zip(native,requests):
            if meta['stage']!=q['stage'] or type(meta['generated_tokens']) is not int or not 0<meta['generated_tokens']<=128 or type(meta['prompt_tokens']) is not int or not 0<meta['prompt_tokens']<=8064:
                raise ValueError('native stage/token budget differs')
            if processor is not None:
                inputs=processor.apply_chat_template(q['messages'],add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=False)
                if inputs['input_ids'].shape[1]!=meta['prompt_tokens']:raise ValueError('actual native prompt token count differs')
    return dict(task_id=case['id'],success=record['success'],calls=record['calls'],accepted_edits=accepted_edits,filled_required_slots=filled_slots,
                protocol_errors=protocol_errors,numerical_studies_checked=len(references),references=references,terminal=record['terminal'])


def campaign(root,anchor,plan,plan_anchor,out):
    if verify(root)!=anchor:raise ValueError('owner physics run differs')
    r=read_json(Path(root)/'summary.json')
    if r.get('schema')!='power-grid-env.physics-curriculum-result/v1' or r.get('complete') is not True or r['plan_manifest_sha256']!=plan_anchor:raise ValueError('complete matching physics run required')
    from benchmarks.engineering_physics_campaign import load_plan
    data,p=load_plan(plan,plan_anchor);rows=[]
    from power_grid_env.qwen35_training import local_target
    from power_grid_env.vlm_batches import local_qwen35_processor
    t=local_target('requirements/qwen35-08b-local-target.json',p['target_sha256']);processor=local_qwen35_processor(t['local_checkpoint'],t['config_sha256'])
    for candidate in ('adapter40','physics78'):
        for c in data.cases(p['case_ids']):
            trace=read_json(Path(root)/'episodes'/(candidate+'-'+c['id']+'.json'))
            if trace['candidate']!=candidate:raise ValueError('staged checkpoint attribution differs')
            rows.append(dict(candidate=candidate,**replay(c,trace,processor)))
    skill_scores={}
    for candidate,key in [('adapter40','before'),('physics78','after')]:
        values=r[key]
        if [x['index'] for x in values]!=p['indices']+p['transfer_indices']:raise ValueError('skill evaluation inventory differs')
        for row in values:
            _,answer=data.messages(row['index'])
            if row['expected']!=answer or row['candidate']!=candidate or row['skill']!=data.rows[row['index']]['skill'] or score(row['completion'],answer)!={k:row[k] for k in ('format_valid','exact')}:
                raise ValueError('generated skill answer does not replay')
        skill_scores[candidate]=sum(x['exact'] for x in values)
    workflow_scores={c:sum(x['success'] for x in rows if x['candidate']==c) for c in ('adapter40','physics78')}
    expected=dict(before_skills=skill_scores['adapter40'],after_skills=skill_scores['physics78'],skill_targets=15,
                  before_workflows=workflow_scores['adapter40'],after_workflows=workflow_scores['physics78'],workflow_tasks=3)
    if r['score']!=expected or r['gate_passed']!=(skill_scores['physics78']==15 and workflow_scores['physics78']==3):raise ValueError('reported physics scores/gate differ')
    result=dict(schema='power-grid-env.staged-campaign-audit/v1',source_manifest_sha256=anchor,plan_manifest_sha256=plan_anchor,rows=rows,score=expected,
                replay_passed=True,native_prompt_budgets_checked=True,gate_passed=r['gate_passed'],
                scope='Replayed raw generated decisions,actual tool responses,budgets and constant-Z numerical reference;not model rerun,heldout success or signed provenance')
    write_json(Path(out)/'summary.json',result);seal(out);return result
