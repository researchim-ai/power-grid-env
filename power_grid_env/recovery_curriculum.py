"""Past-only stator-I2 reset diagnosis tools, SFT episodes and anchored scoring.

This is a diagnostic curriculum from qualified simulations, not a trained
agent, a VLM dataset or permission to operate real equipment.
"""
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from power_grid_env.study_artifacts import encoded, read_json, digest, seal, verify
from power_grid_env.evidence import package_fingerprint


class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class ResetObservation(Contract):
    generator_id:str=Field(min_length=1)
    time_s:float=Field(ge=0)
    connected:StrictBool
    latched:StrictBool
    level:float=Field(ge=0)
    reset_level:float | None=Field(default=None,ge=0)


class ResetDecision(Contract):
    action:Literal['request_reset','wait','hold']
    reason:Literal['eligible','thermal_reset_disabled','generator_connected','thermal_level_above_reset','not_latched']


TOOL_SCHEMAS=[dict(type='function',function=dict(name=name,description=description,
    parameters=dict(type='object',properties=dict(generator_id=dict(type='string')),required=['generator_id'],additionalProperties=False)))
    for name,description in [('inspect_generator','Read the current connected, stator-I2 level and latched indications.'),
                             ('read_reset_policy','Read the declared stator-I2 acknowledgement threshold and constraints.')]]


def diagnose(observation):
    p=ResetObservation.model_validate(observation)
    if p.reset_level is None:return ResetDecision(action='hold',reason='thermal_reset_disabled')
    if p.connected:return ResetDecision(action='hold',reason='generator_connected')
    if p.level>p.reset_level:return ResetDecision(action='wait',reason='thermal_level_above_reset')
    if not p.latched:return ResetDecision(action='hold',reason='not_latched')
    return ResetDecision(action='request_reset',reason='eligible')


class ResetDiagnosticSession:
    """Read-only tools expose one observed instant, never a future trace."""
    def __init__(self,observation):
        self._observation=ResetObservation.model_validate(observation).model_dump(mode='json')
        self._read=set();self.submitted=None

    def call(self,name,arguments):
        if not isinstance(arguments,dict):return dict(ok=False,error='object_arguments_required')
        if name in ('inspect_generator','read_reset_policy'):
            if arguments!={'generator_id':self._observation['generator_id']}:return dict(ok=False,error='exact_generator_target_required')
            self._read.add(name);p=self._observation
            if name=='inspect_generator':
                return dict(ok=True,observation={k:p[k] for k in ('generator_id','time_s','connected','latched','level')},units='dimensionless stator-I2 replica')
            return dict(ok=True,policy=dict(reset_level=p['reset_level'],requires_open_generator=True,
                requires_explicit_request=True,retains_heat=True,does_not_close_breaker=True,
                does_not_clear_operator_inhibit=True))
        if name=='submit_decision':
            if self._read!={'inspect_generator','read_reset_policy'}:return dict(ok=False,error='inspect_and_read_policy_first')
            if self.submitted is not None:return dict(ok=False,error='already_submitted')
            try:self.submitted=ResetDecision.model_validate(arguments).model_dump(mode='json')
            except ValueError:return dict(ok=False,error='invalid_decision')
            return dict(ok=True,recorded=True,physical_action_executed=False)
        return dict(ok=False,error='unknown_tool')


def teacher_episode(observation):
    p=ResetObservation.model_validate(observation);session=ResetDiagnosticSession(p)
    messages=[dict(role='user',content='Diagnose whether an explicit stator-I2 reset request is currently admissible for generator '+p.generator_id+'. Inspect the current indication and reset policy. Return only a JSON decision with action and reason. Reset does not imply permission to close.')]
    for i,name in enumerate(('inspect_generator','read_reset_policy')):
        args=dict(generator_id=p.generator_id);id_='call_'+str(i)
        messages.append(dict(role='assistant',content=None,tool_calls=[dict(id=id_,type='function',function=dict(name=name,arguments=encoded(args).decode()))]))
        messages.append(dict(role='tool',tool_call_id=id_,content=encoded(session.call(name,args)).decode()))
    messages.append(dict(role='assistant',content=encoded(diagnose(p).model_dump(mode='json')).decode()))
    return messages


def build(qualified,accepted,out):
    from power_grid_env.study_spec import StudySpec
    from power_grid_env.model import canonical_hash
    source,consumer=Path(qualified).resolve(),Path(accepted).resolve();target=Path(out).resolve()
    if any(target.is_relative_to(p) for p in (source,consumer)):raise ValueError('corpus must be outside sealed evidence')
    anchors={str(p):verify(p) for p in (source,consumer)};q,a=[read_json(p/'summary.json') for p in (source,consumer)];fp=package_fingerprint()
    if not all(s['passed'] and s['code_sha256']==fp and (s['native_runs'],s['independent_trajectories'],s['comparisons'])==(80,40,160) for s in (q,a)) or a['source_manifest_sha256']!=anchors[str(source)] or a['actions']!=q['actions']:
        raise ValueError('qualified physics and independent consumer required')
    rows=[]
    for item in q['rows']:
        if item['id'].endswith('-reversed'):continue
        child=source/item['id']/ 'radau-1';verify(child)
        study=StudySpec.model_validate(read_json(child/'study.json'));trace=read_json(child/'trace.json')
        if not trace['completed'] or trace['code_sha256']!=fp or trace['settings']!=study.coupled.model_dump(mode='json') or trace['network_sha256']!=canonical_hash(study.network):raise ValueError('trace owner/runtime differs')
        # All disturbances/temperatures of this same topology stay in TRAIN.
        # This corpus does not manufacture a held-out topology evaluation.
        family=digest(encoded(study.network.model_dump(mode='json')))
        for index,event in enumerate(trace['event_log']):
            if event['kind']!='generator_thermal_reset':continue
            t=event['time_s'];indices=[i for i,(time,side) in enumerate(zip(trace['time_s'],trace['sample_side'])) if time==t and side=='pre_event']
            if len(indices)!=1:raise ValueError('unique pre-command observation required')
            i=indices[0];g=event['equipment_id'];p=next(p for p in study.coupled.generator_thermal if p.generator_id==g)
            observation=ResetObservation(generator_id=g,time_s=t,connected=bool(trace['channels'][g+'.connected'][i]),
                latched=bool(trace['channels'][g+'.stator_thermal_latched'][i]),level=trace['channels'][g+'.stator_thermal_level'][i],reset_level=p.reset_level)
            decision=diagnose(observation)
            outcome='applied' if decision.action=='request_reset' else 'already_in_requested_state' if decision.reason=='not_latched' else 'blocked'
            if event['outcome']!=outcome or (outcome=='blocked' and event.get('reason')!=decision.reason):raise ValueError('diagnosis differs from qualified physical action')
            id_=digest(encoded(dict(leaf=str(child.relative_to(source)),event_index=index)))[:24]
            rows.append(dict(id=id_,family_id=family,split='train',observation=observation.model_dump(mode='json'),
                expected=decision.model_dump(mode='json'),source_trace_sha256=digest((child/'trace.json').read_bytes())))
    if len(rows)!=18 or len({r['id'] for r in rows})!=18:raise ValueError('expected 18 nonduplicated diagnostic instants')
    data=dict(schema='power-grid-env.thermal-reset-curriculum/v1',code_sha256=fp,source_manifests=anchors,rows=rows,
              scope='Train-only scripted diagnostic tool episodes from qualified simulation indications; no future events, topology-held-out evaluation, VLM images, training run or online control reward.')
    if anchors!={str(p):verify(p) for p in (source,consumer)}:raise ValueError('evidence changed')
    target.mkdir(parents=True,exist_ok=False);(target/'corpus.json').write_bytes(encoded(data));seal(target)
    return dict(rows=len(rows),families=len({r['family_id'] for r in rows}),split='train',manifest_sha256=verify(target))


def verify_corpus(root,expected_manifest):
    anchor=verify(root)
    if anchor!=expected_manifest:raise ValueError('owner corpus anchor differs')
    data=read_json(Path(root)/'corpus.json')
    if data['schema']!='power-grid-env.thermal-reset-curriculum/v1' or data['code_sha256']!=package_fingerprint():raise ValueError('corpus schema/runtime differs')
    if len(data['rows'])!=18 or len({r['id'] for r in data['rows']})!=18:raise ValueError('corpus inventory differs')
    for row in data['rows']:
        if row['split']!='train' or row['expected']!=diagnose(row['observation']).model_dump(mode='json'):raise ValueError('diagnostic label/split differs')
    return data


def export(root,out,expected_manifest,kind='sft'):
    if kind not in ('sft','prompts'):raise ValueError('unknown export kind')
    if Path(out).resolve().is_relative_to(Path(root).resolve()):raise ValueError('export outside sealed corpus required')
    data=verify_corpus(root,expected_manifest)
    with Path(out).open('xb') as stream:
        for row in data['rows']:
            record=(dict(messages=teacher_episode(row['observation']),tools=TOOL_SCHEMAS) if kind=='sft' else
                    dict(task_id=row['id'],observation=row['observation'],tools=TOOL_SCHEMAS))
            record['metadata']=dict(task_id=row['id'],family_id=row['family_id'],split='train',corpus_manifest_sha256=expected_manifest)
            stream.write(encoded(record)+b'\n')
    if verify(root)!=expected_manifest:raise ValueError('corpus changed during export')
    return dict(rows=18,kind=kind,split='train')


def score(root,task_id,answer,expected_manifest):
    data=verify_corpus(root,expected_manifest)
    row=next((r for r in data['rows'] if r['id']==task_id),None)
    if row is None:raise ValueError('unknown task')
    try:prediction=ResetDecision.model_validate(answer).model_dump(mode='json')
    except ValueError:return dict(reward=0.,valid=False,scope='diagnosis_only')
    return dict(reward=float(prediction==row['expected']),valid=True,scope='diagnosis_only')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build');b.add_argument('--qualified',required=True);b.add_argument('--accepted',required=True);b.add_argument('--out',required=True)
    e=sub.add_parser('export');e.add_argument('--root',required=True);e.add_argument('--out',required=True);e.add_argument('--expected-manifest',required=True);e.add_argument('--kind',choices=['sft','prompts'],default='sft')
    a=vars(p.parse_args());action=a.pop('action');print(build(**a) if action=='build' else export(**a))
