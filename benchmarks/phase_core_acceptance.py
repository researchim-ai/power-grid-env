"""Coupled core-loss/zero-loss machines, unbalance, mutual L, faults and heat."""
import argparse,copy,hashlib,json
from pathlib import Path
import numpy as np
from power_grid_env.phase_network import PhaseCase
from power_grid_env.phase_research import run_phase_study
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.phase_core_reference import run_reference
ROOT=Path(__file__).resolve().parents[1];NAMES=('algebraic_mixed','thermal_multicage','asymmetric_fault','algebraic_fault','four_cage_sag')
LIMITS={'voltage_v':2e-4,'current_a':2e-4,'current_alpha_a':2e-4,'current_beta_a':2e-4,'motor_speed_rad_s':2e-5,'torque_nm':2e-5,'rotor_copper_w':2e-3,'stator_copper_w':2e-3,'core_loss_w':2e-3,'stator_flux_alpha_vs':2e-7,'stator_flux_beta_vs':2e-7,'stator_temperature_c':2e-7,'rotor_temperature_c':2e-7}


def document(name):
    if name not in NAMES:raise ValueError('unknown registered core scenario')
    p=read_json(ROOT/'cases/studies/flux-saturated_dol.json')['flux_network']['motors'][0];p['core_loss_s']=.006;p['initial_speed_rad_s']=0. if name in {'algebraic_mixed','thermal_multicage','four_cage_sag'} else 20.;p['provenance']['core_loss']='authored 0.006 S eddy-current branch, Aalto equation (5), not measured';p['id']='core';p['node_id']='pcc'
    cages=4 if name=='four_cage_sag' else 3 if name=='thermal_multicage' else 2 if 'fault' in name else 1
    p['cages']+= [dict(resistance_ohm=5.+i,leakage_h=.018+.004*i) for i in range(cages-1)]
    if name in {'thermal_multicage','asymmetric_fault'}:
        p['thermal']=dict(stator_capacity_j_per_k=2000.,rotor_capacity_j_per_k=1000.,stator_ambient_w_per_k=10.,rotor_ambient_w_per_k=8.,cross_w_per_k=2.);p['provenance']['thermal']='authored thermal lump capacities/cooling'
    motors=[dict(id='core',nodes=['a','b','c'],passport=p)]
    if 'mixed' in name or name=='thermal_multicage':
        other=copy.deepcopy(p);other['id']='lossless';other['core_loss_s']=0.;other['thermal']=None;other['initial_speed_rad_s']=-10.;other['cages']=other['cages'][:2]
        motors.append(dict(id='lossless',nodes=['a','b','c'],passport=other,initial_fluxes_ab_vs=[[.03,.02]]+[[.025,.015]]*len(other['cages'])))
    sources=[dict(id='grid'+n,node='s'+n,tones=[dict(order=1,rms=v,phase_rad=angle)],envelope=[(0.,1.)]) for n,v,angle in zip('abc',[230.,190.,220.],[0.,-2*np.pi/3,2*np.pi/3])]
    if name=='four_cage_sag':sources[1]['envelope']=[(0.,1.),(.008,1.),(.012,.3),(.022,.3),(.026,1.)]
    case=dict(id=name,nodes=['sa','sb','sc','a','b','c'],sources=sources,resistors=[dict(id='shunt'+n,frm=n,resistance_ohm=500.) for n in 'abc'],capacitors=[] if name.startswith('algebraic') else [dict(id='C'+n,frm=n,capacitance_f=c) for n,c in zip('abc',[50e-6,70e-6,60e-6])],inductors=[dict(id='line'+n,frm='s'+n,to=n,resistance_ohm=r) for n,r in zip('abc',[.4,.5,.6])],inductance_h=[[.01,.001,.0005],[.001,.012,.0008],[.0005,.0008,.011]],motors=motors,sequence_monitors=dict(pcc=('a','b','c')),provenance=dict(origin='authored SI RLC network; motor electrical/magnetic seed from published Aalto example; extra cages, core conductance, thermal, network, faults are declared authored parameters'))
    if 'fault' in name:
        case['resistors'].append(dict(id='fault',frm='a',to='b',resistance_ohm=5000.));case['events']=[dict(time_s=t,kind='resistance',equipment_id='fault',value=r) for t,r in [(.012,2.),(.024,5000.)]]
    return dict(id=name,mode='emt',case=PhaseCase.model_validate(case).model_dump(mode='json'),emt=dict(duration_s=.2 if name=='thermal_multicage' else .12 if name=='four_cage_sag' else .04,sample_s=.0005,max_step_s=.0002,rtol=1e-9,atol=1e-11))


def sources():
    paths=[Path(__file__),ROOT/'benchmarks/phase_core_reference.py',ROOT/'cases/studies/flux-saturated_dol.json']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def channel_inventory(case):
    known={s['node'] for s in case['sources']};keys=[n+'.voltage_v' for n in case['nodes'] if n not in known]+[b['id']+'.current_a' for b in case['inductors']]
    for m in case['motors']:
        fields=['current_alpha_a','current_beta_a','motor_speed_rad_s','torque_nm','rotor_copper_w']
        if m['passport'].get('core_loss_s',0):
            fields+=['core_loss_w','stator_copper_w','stator_flux_alpha_vs','stator_flux_beta_vs']
            if m['passport']['thermal']:fields+=['stator_temperature_c','rotor_temperature_c']
        keys += [m['id']+'.'+k for k in fields]
    return keys


def comparison(d,primary,reference):
    if primary['time_s']!=reference['time_s'] or primary['sample_side']!=reference['sample_side'] or len(reference['records'])!=len(primary['time_s']) or reference['primary_rhs_used']:raise ValueError('independent phase inventory differs')
    errors={};ratios=[]
    for key in channel_inventory(d['case']):
        a=np.array(primary['channels'][key]);b=np.array([r['channels'][key] for r in reference['records']]);error=float(max(abs(a-b)));limit=LIMITS[key.split('.')[-1]]+1e-6*max(abs(b));errors[key]=error;ratios.append(error/limit)
    energy=abs(primary['energy'][-1]['residual_j'])/max(1.,abs(primary['energy'][-1]['entered_j']),abs(primary['energy'][-1]['loss_j']));re=max(abs(r['energy_error_j']) for r in reference['records'])/max(1.,abs(primary['energy'][-1]['entered_j']),abs(primary['energy'][-1]['loss_j']))
    return dict(errors=errors,maximum_scaled_error=float(max(ratios)),primary_energy_relative_error=energy,reference_energy_relative_error=re,kcl_max_a=primary['kcl_max_a'],passed=bool(max(ratios)<=1 and energy<=1e-5 and re<=1e-6 and primary['kcl_max_a']<=1e-7))


def resource_counts(fine,coarse):
    return {level:dict(states=result['state_count'],rhs_evaluations=result['rhs_evaluations'],exported_samples=len(result['time_s'])) for level,result in [('fine',fine),('coarse',coarse)]}


def qualify(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();rows=[]
    for name in NAMES:
        d=document(name);fine=run_phase_study(d,out/name/'fine');coarse_doc=copy.deepcopy(d);coarse_doc['emt'].update(max_step_s=.0004,rtol=1e-6,atol=1e-8);coarse=run_phase_study(coarse_doc,out/name/'coarse')
        ref=run_reference(d['case'],fine['time_s'],sides=fine['sample_side']);ref.update(time_s=fine['time_s'],sample_side=fine['sample_side']);(out/name/'reference.json').write_bytes(encoded(ref));f=comparison(d,fine,ref);c=comparison(d,coarse,ref)
        row=dict(case=name,resources=resource_counts(fine,coarse),fine=f,coarse=c,improved=f['maximum_scaled_error']<=c['maximum_scaled_error'],passed=f['passed'] and f['maximum_scaled_error']<=c['maximum_scaled_error']);rows.append(row);print(json.dumps({k:row[k] for k in ['case','improved','passed']}),flush=True)
    s=dict(schema='pge-phase-core-qualification/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,passed=bool(all(r['passed'] for r in rows) and producer==package_fingerprint() and hashes==sources()),reference='separate all-flux three-phase RLC KCL/ODE, DOP853, no production motor/network RHS',core_loss_model='constant eddy-current conductance; not hysteresis',manufacturer_accuracy_proven=False,superiority_proven=False)
    (out/'summary.json').write_bytes(encoded(s));seal(out);return s


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['schema']!='pge-phase-core-qualification/v1' or s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=sources():raise ValueError('current phase core producers required')
    for name in NAMES:
        d=document(name);ref=read_json(out/name/'reference.json');checks=[];results=[]
        replay=run_reference(d['case'],ref['time_s'],sides=ref['sample_side'])
        if len(replay['records'])!=len(ref['records']):raise ValueError('reference replay inventory differs')
        for actual,stored in zip(replay['records'],ref['records'],strict=True):
            if actual['channels'].keys()!=stored['channels'].keys() or any(abs(actual['channels'][key]-stored['channels'][key])>1e-8*(1+abs(actual['channels'][key])) for key in actual['channels']) or abs(actual['energy_error_j']-stored['energy_error_j'])>1e-7:raise ValueError('independent reference replay differs')
        for level in ['fine','coarse']:
            folder=out/name/level;verify(folder);actual=read_json(folder/'study.json');expected=copy.deepcopy(d)
            if level=='coarse':expected['emt'].update(max_step_s=.0004,rtol=1e-6,atol=1e-8)
            from power_grid_env.phase_research import PhaseStudy
            if actual!=PhaseStudy.model_validate(expected).model_dump(mode='json'):raise ValueError('registered phase input differs')
            result=read_json(folder/'result.json')
            if result['code_sha256']!=package_fingerprint():raise ValueError('native phase producer differs')
            checks.append(comparison(d,result,ref));results.append(result)
        f,c=checks;rows.append(dict(case=name,resources=resource_counts(*results),fine=f,coarse=c,improved=f['maximum_scaled_error']<=c['maximum_scaled_error'],passed=f['passed'] and f['maximum_scaled_error']<=c['maximum_scaled_error']))
    if s['rows']!=rows or s['passed']!=all(r['passed'] for r in rows) or s['manufacturer_accuracy_proven'] or s['superiority_proven']:raise ValueError('phase core qualification conclusion differs')
    return dict(verified=True,passed=s['passed'],manifest_sha256=identity,independent_reference_replayed=True,scope='stored native trajectories, separate reference ODE replay and numerical comparisons; no native replay, authentication or OEM accuracy certificate')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args();r=qualify(a.out) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)
