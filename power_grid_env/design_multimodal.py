"""Version-pinned public visual data from owner-anchored engineering studies.

Physics provenance, exporter identity and diagnostic labels are separate.
The source is re-audited without executing historical solver code or relaxing
its criteria. Prompts never include private targets or future observations.
"""
from pathlib import Path
import copy
import itertools
import json
import math
import re
import shutil
import tempfile
from typing import Literal
from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract
from power_grid_env.network_design import compile_network,verify_network_design
from power_grid_env.network_design_view import observation,single_line_svg
from power_grid_env.design_frame import public_frame,diagnosis,IndicationDecision,MotorIndication,indication_rule
from power_grid_env.design_multimodal_render import rasterize_generated_svg,passport_data,passport_png,waveform_png
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint

SHA=r'^[0-9a-f]{64}$'
MODES=('image','text','combined','schematic')


class ObservationProtocol(Contract):
    schema_version:Literal['power-grid-env.design-observation-protocol/v1']='power-grid-env.design-observation-protocol/v1'
    cutoffs_s:list[float]=Field(min_length=1,max_length=8)
    side:Literal['pre_event','post_event']='post_event'
    family_splits:dict[str,Literal['train','dev','test']]

    @model_validator(mode='after')
    def split_and_time(self):
        if sorted(set(self.cutoffs_s))!=self.cutoffs_s or self.cutoffs_s[0]<0:raise ValueError('sorted unique nonnegative cutoffs required')
        if not self.family_splits or any(not re.fullmatch(SHA,k) for k in self.family_splits):raise ValueError('exact topology-family SHA256 mapping required')
        if set(self.family_splits.values())!={'train','dev','test'}:raise ValueError('nonempty train/dev/test families required')
        return self


def topology_family(graph):
    nodes=[n['node_id'] for n in graph['nodes']]
    if not 2<=len(nodes)<=6 or len(set(nodes))!=len(nodes):raise ValueError('two to six unique sites for exact family grouping')
    index={n:i for i,n in enumerate(nodes)};adj=[[0]*len(nodes) for _ in nodes]
    for b in graph['branches']:
        if b['parent_node'] not in index or b['child_node'] not in index or b['parent_node']==b['child_node']:raise ValueError('invalid family edge')
        a,c=index[b['parent_node']],index[b['child_node']];adj[a][c]+=1;adj[c][a]+=1
    canonical=min(tuple(adj[a][b] for a in order for b in order) for order in itertools.permutations(range(len(nodes))))
    return digest(encoded(dict(nodes=len(nodes),adjacency=canonical)))


def _anchor(root,expected):
    if not isinstance(expected,str) or not re.fullmatch(SHA,expected) or verify(root)!=expected:raise ValueError('trusted owner manifest differs')


def source_inventory(source,accepted,source_manifest,accepted_manifest):
    source,accepted=Path(source).resolve(),Path(accepted).resolve();_anchor(source,source_manifest);_anchor(accepted,accepted_manifest)
    q,a=read_json(source/'summary.json'),read_json(accepted/'receipt.json')
    fp=q.get('code_sha256')
    if not isinstance(fp,str) or not re.fullmatch(SHA,fp) or q.get('passed') is not True or a.get('passed') is not True or a.get('code_sha256')!=fp or a.get('source_manifest_sha256')!=source_manifest:raise ValueError('qualified source and anchored independent acceptance required')
    rows=q.get('rows',[])
    if not 1<=len(rows)<=16 or q.get('designs')!=len(rows) or a.get('designs')!=len(rows) or a.get('verified_dynamic_runs')!=q.get('dynamic_runs'):raise ValueError('source coverage differs')
    if len({r['id'] for r in rows})!=len(rows) or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',r['id']) for r in rows):raise ValueError('safe unique source design IDs required')
    result=[]
    for row in rows:
        root=source/row['id'];task=read_json(root/'task.json');proposal=read_json(root/'proposal.json')
        checked=verify_network_design(root,expected_task=task,expected_proposal=proposal,expected_producer=fp)
        if not checked['completed'] or not checked['numerically_qualified'] or checked['manifest_sha256']!=row['design_manifest']:raise ValueError('qualified physical source required')
        t,p,plan,c=compile_network(task,proposal);graph=observation(t,p)
        result.append(dict(id=row['id'],task=t,proposal=p,plan=plan,graph=graph,family_id=topology_family(graph),
                           root=root,manifest=checked['manifest_sha256']))
    _anchor(source,source_manifest);_anchor(accepted,accepted_manifest)
    return fp,result


def _asset(root,render):
    with tempfile.TemporaryDirectory(prefix='.image-',dir=root) as work:
        path=Path(work)/'image.png';render(path);raw=path.read_bytes()
        if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw)>8_000_000:raise ValueError('bounded PNG image required')
        name='images/'+digest(raw)+'.png';dest=root/name
        if not dest.exists():dest.write_bytes(raw)
        elif dest.read_bytes()!=raw:raise ValueError('image digest collision')
        return name


def build(source,accepted,source_manifest,accepted_manifest,protocol,out):
    protocol=ObservationProtocol.model_validate(protocol)
    fp,designs=source_inventory(source,accepted,source_manifest,accepted_manifest)
    if set(protocol.family_splits)!={d['family_id'] for d in designs}:raise ValueError('exact family split coverage required')
    root=Path(out).resolve()
    if any(root.is_relative_to(Path(p).resolve()) for p in (source,accepted)):raise ValueError('dataset outside sealed sources required')
    root.mkdir(parents=True,exist_ok=False);(root/'images').mkdir();(root/'frames').mkdir()
    rows=[];cache={};exporter=package_fingerprint()
    for d in designs:
        t,p=d['task'],d['proposal'];graph=d['graph'];family=d['family_id']
        sld=_asset(root,lambda path:rasterize_generated_svg(single_line_svg(t,p),path))
        for case in d['plan'].cases:
            leaf=d['root']/'studies'/case.id/'grid-2';trace=read_json(leaf/'result.json')
            refined=case.model_copy(deep=True);refined.study.induction.step_s/=4
            if read_json(leaf/'study.json')!=refined.study.model_dump(mode='json') or trace['code_sha256']!=fp:raise ValueError('source study/producer differs')
            passport=passport_data(t,p,case);key=digest(encoded(passport))
            if key not in cache:cache[key]=_asset(root,lambda path:passport_png(passport,path))
            for cutoff in protocol.cutoffs_s:
                frame=public_frame(refined,trace,cutoff,protocol.side);frame['graph']=graph;frame['passport']=passport
                frame['source']=dict(physics_code_sha256=fp,design_manifest_sha256=d['manifest'],study_sha256=digest(encoded(refined.study.model_dump(mode='json'))),trace_sha256=digest((leaf/'result.json').read_bytes()))
                id_=digest(encoded(dict(design=d['manifest'],study=frame['source']['study_sha256'],cutoff=cutoff,side=protocol.side)))[:24]
                wave=_asset(root,lambda path:waveform_png(frame,path));raw=encoded(frame)
                name='frames/'+id_+'.json';(root/name).write_bytes(raw)
                rows.append(dict(id=id_,family_id=family,split=protocol.family_splits[family],frame=name,frame_sha256=digest(raw),
                                 images=dict(sld=sld,passport=cache[key],waveform=wave),expected=diagnosis(frame).model_dump(mode='json')))
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('duplicate observation IDs')
    families=[dict(id=f,split=protocol.family_splits[f],designs=sorted(d['id'] for d in designs if d['family_id']==f)) for f in sorted(protocol.family_splits)]
    result=dict(schema='power-grid-env.design-multimodal-corpus/v1',physics_code_sha256=fp,exporter_code_sha256=exporter,
        source_manifests={str(Path(source).resolve()):source_manifest,str(Path(accepted).resolve()):accepted_manifest},
        protocol=protocol.model_dump(mode='json'),families=families,rows=rows,
        source_reaudit=dict(verified_grid_runs=sum(len(d['plan'].cases)*3 for d in designs),auditor_code_sha256=exporter,explicit_source_revision_pin=True),
        scope='Past-only displayed-indication classification. Topology-family held-out splits; shared equipment parameters. No trained VLM, independent motor-family holdout, engineering verdict or closed-loop control reward.')
    _anchor(source,source_manifest);_anchor(accepted,accepted_manifest)
    if exporter!=package_fingerprint():raise ValueError('exporter changed')
    (root/'corpus.json').write_bytes(encoded(result));anchor=seal(root)
    return dict(rows=len(rows),families=len(families),images=len(list((root/'images').glob('*.png'))),manifest_sha256=anchor,
                physics_code_sha256=fp,exporter_code_sha256=exporter)


def physics_revisions(data):
    """Versioned provenance; v1 must never silently accept mixed producers."""
    primary=data.get('physics_code_sha256')
    if not isinstance(primary,str) or not re.fullmatch(SHA,primary):
        raise ValueError('explicit primary physics producer required')
    if data.get('schema')=='power-grid-env.design-multimodal-corpus/v1':
        if 'physics_code_sha256s' in data:raise ValueError('mixed producers require corpus v2')
        return [primary]
    if data.get('schema')!='power-grid-env.design-multimodal-corpus/v2':
        raise ValueError('unsupported corpus schema')
    revisions=data.get('physics_code_sha256s')
    if (not isinstance(revisions,list) or not 1<=len(revisions)<=16 or
            any(not isinstance(v,str) or not re.fullmatch(SHA,v) for v in revisions) or
            revisions!=sorted(set(revisions)) or primary not in revisions):
        raise ValueError('sorted bounded exact physics producer inventory required')
    return revisions


def verify_dataset(root,expected_manifest):
    root=Path(root).resolve();_anchor(root,expected_manifest);data=read_json(root/'corpus.json')
    revisions=physics_revisions(data);seen_revisions=set()
    protocol=ObservationProtocol.model_validate(data['protocol']);ids=set();families={}
    if any(not isinstance(data.get(k),str) or not re.fullmatch(SHA,data[k]) for k in ('physics_code_sha256','exporter_code_sha256')):raise ValueError('explicit source and exporter revisions required')
    for row in data['rows']:
        if not re.fullmatch(r'[0-9a-f]{24}',row['id']) or row['id'] in ids or row['frame']!='frames/'+row['id']+'.json':raise ValueError('observation identity differs')
        ids.add(row['id']);path=root/row['frame']
        if digest(path.read_bytes())!=row['frame_sha256']:raise ValueError('frame hash differs')
        frame=read_json(path);family=topology_family(frame['graph'])
        if set(frame)!={'schema','cutoff_s','side','rule','motors','history','scope','graph','passport','source'} or frame['schema']!='power-grid-env.design-frame/v1':raise ValueError('unexpected frame fields')
        if frame['rule']!=indication_rule():raise ValueError('indication rule differs from v1 protocol')
        if not any(abs(frame['cutoff_s']-t)<=1e-12 for t in protocol.cutoffs_s) or frame['side'] not in (protocol.side,'regular','initial'):raise ValueError('frame cutoff/side outside owner protocol')
        motors=[MotorIndication.model_validate(m) for m in frame['motors']]
        ids_m={m.motor_id for m in motors}
        if not 1<=len(motors)<=8 or len(ids_m)!=len(motors) or ids_m!={m['id'] for m in frame['passport']['motors']}:raise ValueError('motor/passport coverage differs')
        if family!=row['family_id'] or row['split']!=protocol.family_splits.get(family) or row['expected']!=diagnosis(frame).model_dump(mode='json'):raise ValueError('split or target differs from observation')
        families[family]=row['split'];h=frame['history'];times=h['time_s']
        if set(h)!={'time_s','sample_side','channels'} or set(h['channels'])!={m.motor_id+'.'+s for m in motors for s in ('connected','speed_pu','current_pu','bus_voltage_pu')}:raise ValueError('unexpected observation channels')
        if not times or any(type(t) not in (int,float) or not math.isfinite(t) for t in times) or times[-1]!=frame['cutoff_s'] or any(t>frame['cutoff_s'] for t in times) or any(a>b for a,b in zip(times,times[1:])):raise ValueError('future or invalid observed time')
        if len(h['sample_side'])!=len(times) or frame['side']!=h['sample_side'][-1]:raise ValueError('observation side differs')
        for i,side in enumerate(h['sample_side']):
            if side not in ('initial','regular','pre_event','post_event') or (side=='initial')!=(i==0):raise ValueError('invalid observation sample side')
            if side=='post_event' and (i==0 or h['sample_side'][i-1]!='pre_event' or times[i-1]!=times[i]):raise ValueError('missing observed pre-event side')
            if side=='pre_event' and i<len(times)-1 and (h['sample_side'][i+1]!='post_event' or times[i+1]!=times[i]):raise ValueError('missing observed post-event side')
        for m in frame['motors']:
            for suffix,key in [('connected','connected'),('speed_pu','speed_indication_pu'),('current_pu','current_indication_pu'),('bus_voltage_pu','voltage_indication_pu')]:
                values=h['channels'][m['motor_id']+'.'+suffix]
                if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):raise ValueError('invalid history values')
                if suffix=='connected' and any(v not in (0,1) for v in values):raise ValueError('invalid connection indication')
                if len(values)!=len(times) or (bool(values[-1]) if suffix=='connected' else round(values[-1],3))!=m[key]:raise ValueError('snapshot differs from observed prefix')
        revision=frame['source']['physics_code_sha256']
        if revision not in revisions:raise ValueError('source revision differs')
        seen_revisions.add(revision)
        if set(row['images'])!={'sld','passport','waveform'}:raise ValueError('three image roles required')
        for name in row['images'].values():
            if not re.fullmatch(r'images/[0-9a-f]{64}\.png',name) or digest((root/name).read_bytes())!=Path(name).stem:raise ValueError('image identity differs')
    if set(families)!=set(protocol.family_splits) or not ids or len(data['families'])!=len(families) or {f['id']:f['split'] for f in data['families']}!=families:raise ValueError('family inventory differs')
    if seen_revisions!=set(revisions):raise ValueError('unused or missing physics producer revision')
    _anchor(root,expected_manifest);return data


def messages(frame,images,mode):
    if mode not in MODES:raise ValueError('unknown modality')
    rule=frame['rule'];text=(f'Read motor indications at {frame["cutoff_s"]:.3f} s ({frame["side"]}). '
        f'Use the displayed speed rounded to 0.001 pu. Disconnected motors are "disconnected"; connected motors with displayed speed >= {rule["running_speed_pu"]:.3f} pu are "at_running_speed", otherwise "below_running_speed". '
        'Return JSON with motors and insufficient_observation. If current indications are absent, return motors={} and insufficient_observation=true. Otherwise map every observed motor ID to its indication class and set insufficient_observation=false. This task concerns current indications only.')
    content=[dict(type='text',text=text)]
    if mode in ('image','combined','schematic'):
        names=('sld','passport') if mode=='schematic' else ('sld','passport','waveform')
        for name in names:content += [dict(type='text',text={'sld':'Proposed single-line connections','passport':'Static numeric equipment/model passport','waveform':'Observed history and current indication display'}[name]),dict(type='image',image=images[name])]
    if mode in ('text','combined'):content.append(dict(type='text',text='Instrument observation: '+encoded(dict(rule=rule,motors=[MotorIndication.model_validate(m).model_dump(mode='json') for m in frame['motors']])).decode()))
    return [dict(role='user',content=content)]


def export(root,out,expected_manifest,split='train',kind='sft',mode='image'):
    root=Path(root).resolve();target=Path(out).resolve();data=verify_dataset(root,expected_manifest)
    if kind not in ('sft','prompts') or mode not in MODES or split not in ('train','dev','test'):raise ValueError('invalid export profile')
    if kind=='sft' and split!='train':raise ValueError('SFT may use training families only')
    if target.is_relative_to(root):raise ValueError('export outside sealed corpus required')
    selected=[r for r in data['rows'] if r['split']==split]
    if not selected:raise ValueError('empty split')
    target.mkdir(parents=True,exist_ok=False);(target/'images').mkdir();assets=set()
    with (target/'messages.jsonl').open('xb') as stream:
        for row in selected:
            frame=read_json(root/row['frame']);conversation=messages(frame,row['images'],mode)
            if kind=='sft':conversation.append(dict(role='assistant',content=encoded(diagnosis(frame,mode).model_dump(mode='json')).decode()))
            for block in conversation[0]['content']:
                if block['type']=='image':assets.add(block['image'])
            record=dict(messages=conversation,metadata=dict(task_id=row['id'],family_id=row['family_id'],split=split,modality=mode,corpus_manifest_sha256=expected_manifest))
            if data['schema'].endswith('/v2'):
                record['metadata']['physics_code_sha256']=frame['source']['physics_code_sha256']
            stream.write(encoded(record)+b'\n')
    for name in sorted(assets):shutil.copyfile(root/name,target/name)
    receipt=dict(schema='power-grid-env.design-multimodal-export/v1',passed=True,rows=len(selected),split=split,kind=kind,mode=mode,
        dataset_manifest_sha256=expected_manifest,physics_code_sha256=data['physics_code_sha256'],exporter_code_sha256=package_fingerprint(),images=len(assets),
        messages_sha256=digest((target/'messages.jsonl').read_bytes()),model_trained=False)
    if data['schema'].endswith('/v2'):
        revisions=sorted({read_json(root/r['frame'])['source']['physics_code_sha256'] for r in selected})
        receipt.update(schema='power-grid-env.design-multimodal-export/v2',
            physics_code_sha256=revisions[0],physics_code_sha256s=revisions)
    if verify(root)!=expected_manifest:raise ValueError('corpus changed')
    (target/'receipt.json').write_bytes(encoded(receipt));seal(target);return receipt


class IndicationGrader:
    """Verify once; immutable target tuples avoid re-reading a corpus per sample.

    A host owns this snapshot and the modality. Candidate answers cannot change
    the observation, split, rule, source revision or modality used for scoring.
    """
    def __init__(self,root,expected_manifest):
        data=verify_dataset(root,expected_manifest)
        self._targets={r['id']:tuple(sorted(r['expected']['motors'].items())) for r in data['rows']}
        self.manifest_sha256=expected_manifest

    def score(self,task_id,answer,mode='image'):
        if mode not in MODES:raise ValueError('unknown observation mode')
        if task_id not in self._targets:raise ValueError('unknown task')
        try:decision=IndicationDecision.model_validate(answer)
        except ValueError:return dict(reward=0.,valid=False,scope='displayed_indication_only')
        expected=() if mode=='schematic' else self._targets[task_id]
        correct=decision.insufficient_observation==(mode=='schematic') and tuple(sorted(decision.motors.items()))==expected
        return dict(reward=float(correct),valid=True,scope='displayed_indication_only')

    def reward_batch(self,completions,task_ids,mode='image'):
        """Framework-independent grouped-completion reward callback.

        The host supplies task IDs/modality. Plain text or a single assistant
        message is accepted; duplicate JSON keys, extra prose and malformed
        outputs receive zero. This rewards indication reading, not control.
        """
        if not isinstance(completions,list) or not isinstance(task_ids,list) or len(completions)!=len(task_ids) or len(completions)>4096:raise ValueError('bounded aligned completion/task lists required')
        if mode not in MODES or any(id_ not in self._targets for id_ in task_ids):raise ValueError('unknown host modality/task')
        def unique(items):
            result={}
            for key,value in items:
                if key in result:raise ValueError('duplicate reply key')
                result[key]=value
            return result
        def reject(value):raise ValueError('nonfinite reply')
        rewards=[]
        for task_id,reply in zip(task_ids,completions):
            if isinstance(reply,list) and len(reply)==1 and isinstance(reply[0],dict) and set(reply[0])=={'role','content'} and reply[0]['role']=='assistant':reply=reply[0]['content']
            if not isinstance(reply,str):rewards.append(0.);continue
            try:oversized=len(reply.encode('utf8'))>32768
            except UnicodeError:rewards.append(0.);continue
            if oversized:rewards.append(0.);continue
            try:answer=json.loads(reply,object_pairs_hook=unique,parse_constant=reject)
            except (ValueError,RecursionError):rewards.append(0.);continue
            rewards.append(self.score(task_id,answer,mode)['reward'])
        return rewards


def score(root,task_id,answer,expected_manifest,mode='image'):
    return IndicationGrader(root,expected_manifest).score(task_id,answer,mode)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build')
    for name in ('source','accepted','source-manifest','accepted-manifest','protocol','out'):b.add_argument('--'+name,required=True)
    e=sub.add_parser('export')
    for name in ('root','out','expected-manifest'):e.add_argument('--'+name,required=True)
    e.add_argument('--split',choices=['train','dev','test'],default='train');e.add_argument('--kind',choices=['sft','prompts'],default='sft');e.add_argument('--mode',choices=MODES,default='image')
    v=sub.add_parser('verify');v.add_argument('--root',required=True);v.add_argument('--expected-manifest',required=True)
    args=vars(p.parse_args());action=args.pop('action')
    if action=='build':args['protocol']=read_json(args['protocol']);print(build(**args))
    elif action=='export':print(export(**args))
    else:print(dict(verified=True,rows=len(verify_dataset(**args)['rows'])))
