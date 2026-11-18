"""Authored fixed load sites; candidates choose routes, cables and transformer."""
import copy
from pathlib import Path
from power_grid_env.study_artifacts import read_json

ROOT=Path(__file__).resolve().parents[1]


def example(motors=2,short=False):
    if motors not in (2,3):raise ValueError('two/three motor fixture')
    base=read_json(ROOT/'cases/equipment-design/task.json');catalog=read_json(ROOT/'cases/equipment-catalog/catalog.json')
    original=copy.deepcopy(base['mandatory_cases'][0]);s=original['study'];n=s['network'];settings=s['induction']
    for i in range(2,motors+1):
        node=copy.deepcopy(n['connectivity_nodes'][1]);node['id']='n'+str(i);n['connectivity_nodes'].append(node)
        line=copy.deepcopy(n['lines'][0]);line['id']='template-'+str(i);line['terminal_from']='tf'+str(i);line['terminal_to']='tt'+str(i);n['lines'].append(line)
        n['terminals'] += [dict(id=line['terminal_from'],equipment_id=line['id'],connectivity_node_id='n0',phases=['A','B','C']),dict(id=line['terminal_to'],equipment_id=line['id'],connectivity_node_id=node['id'],phases=['A','B','C'])]
        m=copy.deepcopy(settings['motors'][0]);m.update(id='m'+str(i),node_id=node['id']);settings['motors'].append(m)
        criteria=copy.deepcopy(original['motors'][0]);criteria['motor_id']=m['id'];original['motors'].append(criteria)
    original['parameter_provenance']='Authored 75kVA single-cage motor sites and routes; existing published-extract catalog with explicitly unverified assumptions; no OEM motor accuracy.'
    settings.update(duration_s=.1 if short else 4.,step_s=.001 if short else .002,events=[])
    if short:
        for c in original['motors']:c['minimum_final_speed_pu']=0.
    duration=settings['duration_s']
    original['voltage_windows']=[dict(node_id='n'+str(i),from_s=0,to_s=duration,minimum_voltage_pu=0. if short else .3,maximum_voltage_pu=1.1) for i in range(1,motors+1)]
    cases=[]
    for kind in (('simultaneous',) if short else ('simultaneous','staged','restart')):
        c=copy.deepcopy(original);c['id']=kind;cfg=c['study']['induction']
        if kind=='staged':
            for i,m in enumerate(cfg['motors'][1:],1):m['connected']=False;cfg['events'].append(dict(time_s=.4*i,kind='motor_close',equipment_id=m['id']))
        if kind=='restart':
            cfg['events']=[dict(time_s=t,kind='source_voltage',equipment_id=n['sources'][0]['id'],value=v) for t,v in ((1.5,0.),(1.6,1.))]
            c['voltage_windows']=[dict(node_id='n'+str(i),from_s=a,to_s=b,minimum_voltage_pu=.3,maximum_voltage_pu=1.1) for i in range(1,motors+1) for a,b in ((0,1.5),(1.6,4.))]
            for w in c['voltage_windows']:
                if w['to_s']==1.5:w['to_s']=1.499
        cases.append(c)
    installation=copy.deepcopy(read_json(ROOT/'cases/equipment-catalog/brief.json')['installation'])
    routes=[dict(id='c0'+str(i),node_a='n0',node_b='n'+str(i),installation=installation|dict(length_m=30.+10*i)) for i in range(1,motors+1)]
    routes += [dict(id='c12',node_a='n1',node_b='n2',installation=installation|dict(length_m=20.)),dict(id='long12',node_a='n1',node_b='n2',installation=installation|dict(length_m=600.))]
    task=dict(id='construct-'+str(motors),catalog=catalog,corridors=routes,node_demand_kva={'n0':0.,**{'n'+str(i):77. if i==1 else 75. for i in range(1,motors+1)}},lagging_power_factor=.85,maximum_projected_drop_percent=5.,mandatory_cases=cases)
    return task


def proposal(kind='star',motors=2,transformer='trihal36-400'):
    ids=['c0'+str(i) for i in range(1,motors+1)]
    if kind in ('chain','long'):ids.remove('c02');ids.append('c12' if kind=='chain' else 'long12')
    elif kind!='star':raise ValueError('unknown proposal')
    return dict(transformer_id=transformer,branches=[dict(corridor_id=id_,cable_id='nexans-084193001') for id_ in ids])
