import copy
import json
from pathlib import Path
import pytest
from PIL import Image
from benchmarks.network_construction_cases import example,proposal
from power_grid_env.network_design import run_network_design,verify_network_design
from power_grid_env.network_design_view import observation
from power_grid_env.industrial_dol import IndustrialDOLCase
from power_grid_env.design_frame import public_frame,diagnosis,extrema_indices
from power_grid_env.design_multimodal import topology_family,ObservationProtocol,verify_dataset,export,IndicationGrader
from power_grid_env.design_multimodal_render import waveform_png
from power_grid_env.study_artifacts import encoded,digest,read_json,seal
from power_grid_env.evidence import package_fingerprint


def case_trace(motors=2):
    case=IndustrialDOLCase.model_validate(example(motors)['mandatory_cases'][0])
    times=[0.,.4,1.5,1.5,1.6,1.6,4.];sides=['initial','regular','pre_event','post_event','pre_event','post_event','regular'];channels={}
    for i,m in enumerate(case.study.induction.motors):
        channels.update({m.id+'.speed_pu':[0.,.2,.8996,.8996,.9,.9,.95] if i==0 else [0.,0.,0.,0.,.1,.1,.3],
            m.id+'.connected':[1.,1.,1.,1.,1.,1.,1.] if i==0 else [0.,0.,0.,1.,1.,1.,1.],
            m.id+'.current_pu':[5.,4.,2.,3.,3.,4.,1.],m.id+'.bus_voltage_pu':[1.,.9,.95,0.,0.,.9,.95]})
    return case,dict(completed=True,time_s=times,sample_side=sides,channels=channels)


def test_exact_event_sides_and_display_quantization():
    c,r=case_trace();pre=public_frame(c,r,1.5,'pre_event');post=public_frame(c,r,1.5,'post_event')
    assert pre['motors'][1]['connected'] is False and post['motors'][1]['connected'] is True
    assert pre['history']['channels']['m.bus_voltage_pu'][-1]==.95
    assert post['history']['channels']['m.bus_voltage_pu'][-1]==0.
    assert diagnosis(pre).motors['m2']=='disconnected' and diagnosis(post).motors['m2']=='below_running_speed'
    assert post['motors'][0]['speed_indication_pu']==.9 and diagnosis(post).motors['m']=='at_running_speed'


def test_future_channels_and_event_log_cannot_change_public_frame_or_image(tmp_path):
    c,r=case_trace();before=public_frame(c,r,.4);mutated=copy.deepcopy(r)
    for values in mutated['channels'].values():values[2:]=[9999.]*5
    mutated['event_log']=[dict(time_s=3.9,kind='future_failure',secret='oracle')]
    after=public_frame(c,mutated,.4);assert before==after
    waveform_png(before,tmp_path/'a.png');waveform_png(after,tmp_path/'b.png')
    assert (tmp_path/'a.png').read_bytes()==(tmp_path/'b.png').read_bytes()


@pytest.mark.parametrize('fault',['missing_cut','negative_cut','invalid_side','missing_channel','nan_past','bad_connection','incomplete'])
def test_invalid_observation_cannot_be_synthesized(fault):
    c,r=case_trace();cut=.4;side='post_event'
    if fault=='missing_cut':cut=.3
    elif fault=='negative_cut':cut=-1
    elif fault=='invalid_side':side='future'
    elif fault=='missing_channel':r['channels'].pop('m.current_pu')
    elif fault=='nan_past':r['channels']['m.speed_pu'][1]=float('nan')
    elif fault=='bad_connection':r['channels']['m.connected'][1]=.5
    else:r['completed']=False
    with pytest.raises(ValueError):public_frame(c,r,cut,side)


def test_envelope_keeps_spikes_and_both_event_sides():
    t=[i*.01 for i in range(1000)];y=[0.]*1000;s=['regular']*1000
    y[111]=1000;y[712]=-900;t[501]=t[500];s[500:502]=['pre_event','post_event']
    keep=extrema_indices(t,y,s,buckets=10)
    assert {0,111,500,501,712,999}<=set(keep) and len(keep)<40


def test_relabelled_and_rooted_path_variants_share_a_family():
    a=observation(example(),proposal('star'));b=observation(example(),proposal('chain'))
    assert topology_family(a)==topology_family(b)
    mapping={n['node_id']:'renamed'+str(i) for i,n in enumerate(a['nodes'])};renamed=copy.deepcopy(a)
    for n in renamed['nodes']:n['node_id']=mapping[n['node_id']]
    for edge in renamed['branches']:
        for k in ('parent_node','child_node'):edge[k]=mapping[edge[k]]
    assert topology_family(a)==topology_family(renamed)


@pytest.fixture
def corpus(tmp_path):
    root=tmp_path/'corpus';(root/'frames').mkdir(parents=True);(root/'images').mkdir()
    png=tmp_path/'one.png';Image.new('RGB',(32,32),'white').save(png)
    name='images/'+digest(png.read_bytes())+'.png';(root/name).write_bytes(png.read_bytes())
    rows=[];families=[];splits={}
    for i,(count,kind,split) in enumerate(((2,'star','train'),(3,'star','dev'),(3,'long','test'))):
        c,r=case_trace(count);frame=public_frame(c,r,1.5);frame['graph']=observation(example(count),proposal(kind,count))
        frame['passport']=dict(motors=[dict(id=m.id) for m in c.study.induction.motors]);frame['source']=dict(physics_code_sha256='a'*64)
        family=topology_family(frame['graph']);splits[family]=split;id_=str(i)*24;filename='frames/'+id_+'.json';raw=encoded(frame);(root/filename).write_bytes(raw)
        rows.append(dict(id=id_,family_id=family,split=split,frame=filename,frame_sha256=digest(raw),images=dict(sld=name,passport=name,waveform=name),expected=diagnosis(frame).model_dump(mode='json')))
        families.append(dict(id=family,split=split))
    protocol=ObservationProtocol(cutoffs_s=[1.5],family_splits=splits).model_dump(mode='json')
    data=dict(schema='power-grid-env.design-multimodal-corpus/v1',physics_code_sha256='a'*64,exporter_code_sha256=package_fingerprint(),protocol=protocol,families=families,rows=rows)
    (root/'corpus.json').write_bytes(encoded(data));return root,seal(root)


def test_exports_are_portable_train_only_and_do_not_ship_private_targets(corpus,tmp_path):
    root,anchor=corpus
    for mode in ('image','text','combined','schematic'):
        out=tmp_path/('export-'+mode);receipt=export(root,out,anchor,mode=mode)
        assert receipt['rows']==1 and receipt['split']=='train'
        record=json.loads((out/'messages.jsonl').read_text())
        assert len(record['messages'])==2 and not (out/'frames').exists() and not (out/'corpus.json').exists()
        blocks=record['messages'][0]['content'];images=[b['image'] for b in blocks if b['type']=='image']
        assert len(images)==(0 if mode=='text' else 2 if mode=='schematic' else 3)
        assert all(not Path(p).is_absolute() and (out/p).exists() for p in images)
        if mode=='image':assert 'speed_indication_pu' not in json.dumps(record['messages'][0])
        if mode=='schematic':assert json.loads(record['messages'][1]['content'])==dict(motors={},insufficient_observation=True)
    for split in ('dev','test'):
        with pytest.raises(ValueError):export(root,tmp_path/('bad-'+split),anchor,split=split)
        out=tmp_path/('prompt-'+split);export(root,out,anchor,split=split,kind='prompts')
        assert len(json.loads((out/'messages.jsonl').read_text())['messages'])==1


def test_anchored_cached_grader_rejects_reward_hacking_and_missing_observations(corpus):
    root,anchor=corpus;grader=IndicationGrader(root,anchor);data=verify_dataset(root,anchor);row=data['rows'][0]
    assert grader.score(row['id'],row['expected'])['reward']==1.
    assert grader.score(row['id'],row['expected']|dict(threshold=0.))['reward']==0.
    assert grader.score(row['id'],row['expected']|dict(mode='schematic'))['reward']==0.
    assert grader.score(row['id'],dict(motors={},insufficient_observation=False))['reward']==0.
    assert grader.score(row['id'],dict(motors={},insufficient_observation=True),mode='schematic')['reward']==1.
    assert grader.score(row['id'],row['expected'],mode='schematic')['reward']==0.
    (root/'corpus.json').write_bytes(b'changed on disk')
    assert grader.score(row['id'],row['expected'])['reward']==1.
    with pytest.raises(ValueError):IndicationGrader(root,anchor)


def test_grouped_rewards_parse_strict_model_completions(corpus):
    root,anchor=corpus;data=verify_dataset(root,anchor);row=data['rows'][0];g=IndicationGrader(root,anchor)
    good=encoded(row['expected']).decode()
    replies=[good,[dict(role='assistant',content=good)],'prose '+good,'{"motors":{},"motors":{},"insufficient_observation":true}',good[:-1],dict(motors={})]
    assert g.reward_batch(replies,[row['id']]*6)==[1.,1.,0.,0.,0.,0.]
    assert g.reward_batch(['\ud800'],[row['id']])==[0.]
    assert g.reward_batch(['{"motors":{},"insufficient_observation":true}'],[row['id']],mode='schematic')==[1.]
    with pytest.raises(ValueError):g.reward_batch([good],[])
    with pytest.raises(ValueError):g.reward_batch([good],['foreign-task'])


@pytest.mark.parametrize('mutation',['future','future_horizon','event_side','snapshot','target','split','rule','extra_motor_field','extra_history','image_path','missing_image'])
def test_resealed_semantic_corruption_rejected(corpus,mutation):
    root,_=corpus;data=read_json(root/'corpus.json');row=data['rows'][0];frame=read_json(root/row['frame'])
    if mutation=='future':frame['history']['time_s'][-1]=9.
    elif mutation=='future_horizon':frame['history']['time_s'][-1]=9.;frame['cutoff_s']=9.
    elif mutation=='event_side':frame['side']='pre_event';frame['history']['sample_side'][-1]='pre_event'
    elif mutation=='snapshot':frame['motors'][0]['speed_indication_pu']=.1
    elif mutation=='target':row['expected']['motors']['m']='disconnected'
    elif mutation=='split':row['split']='test'
    elif mutation=='rule':frame['rule']['running_speed_pu']=.1
    elif mutation=='extra_motor_field':frame['motors'][0]['future_speed']=.95
    elif mutation=='extra_history':frame['history']['channels']['future.answer']=[1.]*len(frame['history']['time_s'])
    elif mutation=='image_path':row['images']['sld']='../private.png'
    else:row['images'].pop('waveform')
    raw=encoded(frame);(root/row['frame']).write_bytes(raw);row['frame_sha256']=digest(raw)
    (root/'corpus.json').write_bytes(encoded(data));(root/'manifest.json').unlink();anchor=seal(root)
    with pytest.raises(ValueError):verify_dataset(root,anchor)


def test_invalid_split_protocol_is_not_silently_randomized():
    with pytest.raises(ValueError):ObservationProtocol(cutoffs_s=[1.5,.4],family_splits={'a'*64:'train','b'*64:'dev','c'*64:'test'})
    with pytest.raises(ValueError):ObservationProtocol(cutoffs_s=[.4],family_splits={'a'*64:'train'})


def test_revision_pin_is_explicit_and_does_not_change_identity_or_audits(tmp_path,monkeypatch):
    import power_grid_env.motor_engineering as engineering
    import power_grid_env.motor_trace as trace_module
    task,choice=example(short=True),proposal();root=tmp_path/'run';r=run_network_design(task,choice,root);source=r['code_sha256']
    monkeypatch.setattr(engineering,'package_fingerprint',lambda:'f'*64);monkeypatch.setattr(trace_module,'package_fingerprint',lambda:'f'*64)
    with pytest.raises(ValueError):verify_network_design(root,expected_task=task,expected_proposal=choice)
    checked=verify_network_design(root,expected_task=task,expected_proposal=choice,expected_producer=source)
    assert checked['code_sha256']==source and checked['auditor_code_sha256']=='f'*64 and checked['explicit_revision_pin']
    leaf=root/'studies/simultaneous/grid-0';raw=read_json(leaf/'result.json');raw['network_sha256']='bad'
    plan=read_json(root/'studies/input.json');case=IndustrialDOLCase.model_validate(plan['cases'][0])
    with pytest.raises(ValueError):engineering.audit_motor(case,raw,expected_producer=source)
    with pytest.raises(ValueError):verify_network_design(root,expected_producer='not-a-revision')
