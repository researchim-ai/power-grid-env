"""Owner-side feeder-design curriculum and evidence-backed decision evaluation.

Train/dev/test are disjoint unlabelled line topologies. Private evidence stays
out of prompt exports. This is a data boundary, not an OS/filesystem sandbox.
"""
import itertools
from pathlib import Path
from typing import Literal
from pydantic import Field
from power_grid_env.design_study import (
    Contract,FeederDesignTask,FeederProposal,run_design,verify_design,
)
from power_grid_env.study_artifacts import encoded,digest,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint

FAMILIES=(
    ('radial','train',2,[]),
    ('chain','train',3,[(1,2)]),
    ('fork','dev',4,[(1,2),(1,3)]),
    ('loop','test',4,[(1,2),(2,3),(3,1)]),
    ('two_paths','test',5,[(1,2),(2,4),(4,3),(3,1)]),
    ('ladder','test',6,[(1,2),(1,3),(2,4),(3,5),(4,5),(2,5)]),
)


def topology_key(network):
    """Exact relabelling-invariant multigraph key for this <=6-node profile."""
    nodes=[n.id for n in network.connectivity_nodes]
    if not 2<=len(nodes)<=6 or network.transformers or network.switches:raise ValueError('bounded line-only topology signature required')
    terminals={t.id:t.connectivity_node_id for t in network.terminals};index={n:i for i,n in enumerate(nodes)}
    adjacency=[[0]*len(nodes) for _ in nodes]
    for line in network.lines:
        a=index[terminals[line.terminal_from]];b=index[terminals[line.terminal_to]]
        adjacency[a][b]+=1;adjacency[b][a]+=1
    canonical=min(tuple(adjacency[a][b] for a in order for b in order) for order in itertools.permutations(range(len(nodes))))
    return digest(encoded(dict(nodes=len(nodes),adjacency=canonical)))


def tasks():
    from power_grid_env.build import NetBuilder
    from power_grid_env.induction import InductionMotor,InductionSettings,InductionEvent
    from power_grid_env.motor_thermal import MotorThermalProtection
    from power_grid_env.study_spec import StudySpec
    result=[]
    for i,(family,split,count,edges) in enumerate(FAMILIES):
        b=NetBuilder('design-'+family,'Authored industrial feeder design exercise',10.,s_base_va=1e7)
        for j in range(count):b.node('n'+str(j))
        b.source('n0',.1,.4);b.line_z1('n0','n1',.1+.4j,line_id='feeder')
        for j,(a,c) in enumerate(edges):b.line_z1('n'+str(a),'n'+str(c),.1+.2j,line_id='branch'+str(j))
        for j in range(1,count):b.load('n'+str(j),1000.,200.,model='constant_z',load_id='aux'+str(j))
        motor=InductionMotor(id='m',node_id='n'+str(count-1),rated_va=1e6,rated_ll_v=10000.,stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.3,fan_torque_pu=[.7,.8,.75,.85,.65,.9][i],thermal=MotorThermalProtection(time_constant_s=5.,trip_level=10.,initial_level=0.))
        cases=[]
        events_by_case=[('start',[]),('repeated_sag',[(1.,'source_voltage','source',.5),(1.2,'source_voltage','source',1.),(2.,'source_voltage','source',.65),(2.2,'source_voltage','source',1.)]),('coast_restart',[(1.,'motor_open','m',None),(1.2,'motor_close','m',None)])]
        for name,events in events_by_case:
            settings=InductionSettings(duration_s=4.,step_s=.01,rtol=1e-8,atol=1e-10,motors=[motor.model_copy(deep=True)],event_trace='all',events=[InductionEvent(time_s=t,kind=k,equipment_id=id_,**({'value':v} if v is not None else {})) for t,k,id_,v in events])
            study=StudySpec(id='design-'+family+'-'+name,study='motor_start',backend='scipy',network=b.build(),induction=settings)
            windows=[dict(node_id=motor.node_id,from_s=0,to_s=.95,minimum_voltage_pu=.7,maximum_voltage_pu=1.1),dict(node_id=motor.node_id,from_s=2.4,to_s=4.,minimum_voltage_pu=.7,maximum_voltage_pu=1.1)]
            cases.append(dict(id=name,study=study,parameter_provenance='Authored single-cage 1 MVA/10 kV numerical passport, fan load, auxiliary Z-loads and I2 thermal replica; not OEM/site identification',motors=[dict(motor_id='m',minimum_final_speed_pu=.9,maximum_sampled_current_pu=10.,maximum_thermal_replica_level=[8.,3.,8.,8.,3.,8.][i],expected_thermal_latch=False)],voltage_windows=windows))
        options=[dict(id='opt-a',resistance_ohm=.1,reactance_ohm=.4,provenance='Authored total feeder 0.1+j0.4 ohm, not a cable product'),dict(id='opt-b',resistance_ohm=20.,reactance_ohm=40.,provenance='Authored total feeder 20+j40 ohm, not a cable product')]
        if i%2:
            options[0]['id']='opt-b';options[1]['id']='opt-a'
        options.sort(key=lambda option:option['id'])
        task=FeederDesignTask(id='design-'+family+('-strict' if i in {1,4} else '-standard'),line_id='feeder',options=options,mandatory_cases=cases)
        result.append(dict(family=family,split=split,topology_sha256=topology_key(task.mandatory_cases[0].study.network),task=task))
    keys=[t['topology_sha256'] for t in result]
    if len(set(keys))!=len(keys):raise ValueError('topology leakage across task families')
    return result


def generate_corpus(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();rows=[]
    for item in tasks():
        task=item['task'];folder=out/'tasks'/task.id;folder.mkdir(parents=True)
        (folder/'task.json').write_bytes(encoded(task.model_dump(mode='json')));options=[]
        for option in task.options:
            proposal=dict(option_id=option.id);verdict=run_design(task,proposal,folder/option.id)
            if not verdict['completed'] or not verdict['numerically_qualified']:raise ValueError('corpus needs qualified positive AND negative decisions: '+task.id+'/'+option.id)
            verify_design(folder/option.id,expected_task=task,expected_proposal=proposal)
            options.append(dict(option_id=option.id,status=verdict['status'],reward=verdict['reward']))
        if not any(o['reward']==0. for o in options):raise ValueError('authored tasks must exercise rejected choices')
        rows.append(dict(id=task.id,family=item['family'],split=item['split'],topology_sha256=item['topology_sha256'],task_sha256=digest(encoded(task.model_dump(mode='json'))),feasible=any(o['reward']==1. for o in options),options=options))
        print(task.id,options,flush=True)
    if package_fingerprint()!=code:raise ValueError('corpus producer changed')
    corpus=dict(schema='power-grid-env.feeder-design-corpus/v1',producer_sha256=code,tasks=rows,scope='six authored small single-motor topologies; decision evidence, not trained-agent quality or equipment-sizing qualification')
    (out/'corpus.json').write_bytes(encoded(corpus));seal(out);return corpus


def verify_corpus(root):
    root=Path(root);identity=verify(root);corpus=read_json(root/'corpus.json');registered=tasks();rows=[]
    if corpus['producer_sha256']!=package_fingerprint() or len(corpus['tasks'])!=len(registered):raise ValueError('current complete corpus required')
    for item in registered:
        task=item['task'];folder=root/'tasks'/task.id
        if read_json(folder/'task.json')!=task.model_dump(mode='json'):raise ValueError('registered owner task differs')
        options=[]
        for option in task.options:
            v=verify_design(folder/option.id,expected_task=task,expected_proposal=dict(option_id=option.id))
            if not v['numerically_qualified']:raise ValueError('unqualified corpus reference')
            options.append(dict(option_id=option.id,status=v['status'],reward=v['reward']))
        rows.append(dict(id=task.id,family=item['family'],split=item['split'],topology_sha256=item['topology_sha256'],task_sha256=digest(encoded(task.model_dump(mode='json'))),feasible=any(o['reward']==1. for o in options),options=options))
    expected=dict(schema='power-grid-env.feeder-design-corpus/v1',producer_sha256=package_fingerprint(),tasks=rows,scope='six authored small single-motor topologies; decision evidence, not trained-agent quality or equipment-sizing qualification')
    if corpus!=expected:raise ValueError('corpus verdict, split or scope differs')
    if verify(root)!=identity:raise ValueError('corpus changed during verification')
    return dict(verified=True,manifest_sha256=identity,tasks=len(rows),options=sum(len(r['options']) for r in rows),splits={s:sum(r['split']==s for r in rows) for s in ['train','dev','test']})


def export_dataset(root,out,*,split='train',kind='prompts'):
    if split not in {'train','dev','test'} or kind not in {'prompts','sft'}:raise ValueError('known split/export kind required')
    if kind=='sft' and split!='train':raise ValueError('held-out solutions must not be exported as training labels')
    if Path(out).resolve().is_relative_to(Path(root).resolve()):raise ValueError('export must be outside the sealed corpus')
    checked=verify_corpus(root);corpus=read_json(Path(root)/'corpus.json');rows=[]
    for item in corpus['tasks']:
        if item['split']!=split:continue
        task=read_json(Path(root)/'tasks'/item['id']/'task.json')
        public=dict(task_id=item['id'],task=task)
        if kind=='prompts':rows.append(public)
        else:
            decisions=[dict(option_id=o['option_id']) for o in item['options'] if o['reward']==1.] if item['feasible'] else [dict(no_feasible_option=True)]
            for decision in decisions:
                rows.append(dict(messages=[dict(role='user',content=encoded(public).decode()),dict(role='assistant',content=encoded(decision).decode())],metadata=dict(task_id=item['id'],task_sha256=item['task_sha256'],corpus_manifest_sha256=checked['manifest_sha256'],label_scope='verified feasible choice or whole-catalog infeasibility, not a tool-use demonstration or optimal equipment design')))
    out=Path(out)
    with out.open('xb') as file:
        for row in rows:file.write(encoded(row)+b'\n')
    if verify(root)!=checked['manifest_sha256']:raise ValueError('corpus changed during export')
    return dict(rows=len(rows),split=split,kind=kind,corpus_manifest_sha256=checked['manifest_sha256'])


class NoFeasibleOption(Contract):
    no_feasible_option:Literal[True]


class Prediction(Contract):
    task_id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    proposal:FeederProposal | NoFeasibleOption


def evaluate(root,predictions,out,*,split='test'):
    if split not in {'dev','test'}:raise ValueError('evaluation requires held-out dev/test split')
    if Path(out).resolve().is_relative_to(Path(root).resolve()):raise ValueError('evaluation must be outside the sealed corpus')
    checked=verify_corpus(root);corpus=read_json(Path(root)/'corpus.json')
    if not isinstance(predictions,list) or len(predictions)>len(FAMILIES):raise ValueError('bounded prediction list required')
    values=[Prediction.model_validate(p) for p in predictions];selected={r['id']:r for r in corpus['tasks'] if r['split']==split}
    if len({p.task_id for p in values})!=len(values) or {p.task_id for p in values}!=set(selected):raise ValueError('exact held-out task coverage required')
    rows=[]
    for prediction in values:
        task=selected[prediction.task_id]
        if isinstance(prediction.proposal,NoFeasibleOption):
            rows.append(dict(task_id=prediction.task_id,option_id=None,status='no_feasible_option',reward=0. if task['feasible'] else 1.,task_sha256=task['task_sha256']))
        else:
            option=next((o for o in task['options'] if o['option_id']==prediction.proposal.option_id),None)
            if option is None:raise ValueError('proposal is outside owner catalog')
            rows.append(dict(task_id=prediction.task_id,option_id=option['option_id'],status=option['status'],reward=option['reward'],task_sha256=task['task_sha256']))
    result=dict(schema='power-grid-env.feeder-design-evaluation/v1',split=split,rows=rows,mean_reward=sum(r['reward'] for r in rows)/len(rows),corpus_manifest_sha256=checked['manifest_sha256'],scoring='recomputed cached physical evidence for the exact owner task, proposal and current producer; no new simulation',trained_agent_quality_established=False)
    if verify(root)!=checked['manifest_sha256']:raise ValueError('corpus changed during evaluation')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'predictions.json').write_bytes(encoded([p.model_dump(mode='json') for p in values]));(out/'result.json').write_bytes(encoded(result));seal(out);return result
