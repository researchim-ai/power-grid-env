"""Independent SI GENCLS/two-bus equations and prescribed protection timelines.

No production admittance stamps, machine RHS, initialization or primary event
log is used to solve the reference. Authored cases, not OEM relay certification.
"""
from pathlib import Path
import argparse,json,math
import numpy as np
from scipy.optimize import brentq
from scipy.integrate import solve_ivp
from power_grid_env.build import NetBuilder
from power_grid_env.induction import InductionMotor
from power_grid_env.coupled_rms import CoupledSettings,CoupledEvent,CoupledFault
from power_grid_env.rms_study import ClassicalMachine
from power_grid_env.network_automation import NetworkAutomation
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file

NAMES=('matched_island','overcurrent','breaker_failure','manual_inhibit','measurement_loss','standby_transfer','failed_primary_transfer')
LIMITS=dict(delta=2e-6,omega=2e-8,bus_voltage_pu=2e-7,electrical_power_pu=2e-7,event_time_s=2e-8)


def example(name):
    if name not in NAMES:raise ValueError('unknown automation reference')
    b=NetBuilder('automation-'+name,'Authored two-bus independent SI reference',10.,s_base_va=1e6)
    b.node('grid');b.node('plant');b.source('grid',2.,8.);b.line_z1('grid','plant',.2+1j,line_id='feeder')
    b.load('plant',300000.,100000.,load_id='load',model='constant_z')
    b.generator('plant',300000.,mode='pv',v_set_pu=1.,q_min_var=-1e6,q_max_var=1e6,generator_id='g')
    motor=InductionMotor(id='motor',node_id='plant',rated_va=1e5,rated_ll_v=10000.,stator_r_pu=.02,stator_x_pu=.1,
        rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.5,fan_torque_pu=.6,connected=False)
    breakers=[dict(id='grid-cb',target_kind='source',target_id='source',opening_delay_s=.01),dict(id='line-cb',target_kind='line',target_id='feeder',opening_delay_s=.01)]
    relays=[];events=[];faults=[]
    if name=='matched_island':events=[dict(time_s=.1,kind='source_open',equipment_id='source'),dict(time_s=.4,kind='source_close',equipment_id='source')]
    elif name.endswith('_transfer'):pass
    else:
        faults=[CoupledFault(id='fault',node_id='plant',apply_s=.1,clear_s=.4,r_ohm=1.,x_ohm=0.)]
        relays=[dict(id='oc',kind='overcurrent',measurement_kind='line',measurement_id='feeder',breaker_id='line-cb',pickup=50.,time_multiplier_s=.02)]
    if name=='breaker_failure':breakers[1].update(fail_open=True,breaker_failure_delay_s=.02,backup_breaker_ids=['grid-cb'])
    if name=='manual_inhibit':
        breakers[1].update(maximum_reclose_shots=1,reclose_dead_s=.1)
        events=[dict(time_s=.18,kind='breaker_open',equipment_id='line-cb')]
    if name=='measurement_loss':events=[dict(time_s=.09,kind='relay_measurement_lost',equipment_id='oc')]
    transfers=[];net=b.build()
    if name.endswith('_transfer'):
        net.generators[0].p_w=0.;breakers[0]['fail_open']=name=='failed_primary_transfer'
        breakers.append(dict(id='reserve-cb',target_kind='generator',target_id='g',initial_open=True,closing_delay_s=.01))
        transfers=[dict(id='ats',primary_breaker_id='grid-cb',reserve_breaker_id='reserve-cb',primary_voltage=dict(kind='source',id='source'),reserve_voltage=dict(kind='generator',id='g'),detection_delay_s=.02,dead_time_s=.02)]
        events=[dict(time_s=.1,kind='source_voltage',equipment_id='source',value=0.),dict(time_s=.4,kind='source_voltage',equipment_id='source',value=1.),dict(time_s=.5,kind='source_close',equipment_id='source')]
    cfg=CoupledSettings(duration_s=.6,step_s=.005,motors=[motor],machines=[ClassicalMachine(generator_id='g',rated_va=1e6,inertia_s=1.,damping_pu=1.,x_d_prime_pu=.25)],
        event_policy='coincident_roots_v1',rtol=1e-9,atol=1e-11,events=[CoupledEvent(**e) for e in events],faults=faults,
        network_automation=NetworkAutomation(breakers=breakers,relays=relays,transfers=transfers))
    return StudySpec(id='automation-'+name,study='coupled_rms',backend='scipy',network=net,coupled=cfg)


def reference(name,spec):
    if name not in NAMES or spec.model_dump(mode='json')!=example(name).model_dump(mode='json'):
        raise ValueError('reference supports only the explicitly frozen authored cases')
    if name.endswith('_transfer'):return transfer_reference(name)
    # Explicit SI equivalent, independently declared constants.
    vbase=10000/math.sqrt(3);source=vbase;zgrid=2.2+9j;zg=25j;yl=(300000-100000j)/10000**2
    def initial_power(theta):
        v=vbase*np.exp(1j*theta);ig=yl*v+(v-source)/zgrid
        return 3*(v*ig.conjugate()).real-300000
    theta=brentq(initial_power,-.2,.2,xtol=1e-14);v=vbase*np.exp(1j*theta)
    ig=yl*v+(v-source)/zgrid;emf=v+zg*ig;pm=3*(emf*ig.conjugate()).real/1e6
    eabs=abs(emf);x=np.array([np.angle(emf),1.])
    opened=.13 if name in {'overcurrent','manual_inhibit'} else .16 if name=='breaker_failure' else None
    bounds=sorted(set([0.,.1,.4,.6,*([opened] if opened else []),*([.11] if name=='matched_island' else [])]))
    times=np.r_[np.arange(120)*.005,.6];states={0.:x.copy()};connected=True;fault=False
    def electrical(state,connected,fault):
        eg=eabs*np.exp(1j*state[0]);ygrid=1/zgrid if connected else 0.
        voltage=(eg/zg+ygrid*source)/(1/zg+yl+ygrid+(1. if fault else 0.))
        current=(eg-voltage)/zg;pe=3*(eg*current.conjugate()).real/1e6
        return voltage,pe
    for left,right in zip(bounds,bounds[1:]):
        mid=(left+right)/2
        connected=not (.11<=mid<.4) if name=='matched_island' else not (opened is not None and mid>=opened)
        fault=name!='matched_island' and .1<=mid<.4
        def rhs(t,state):
            _,pe=electrical(state,connected,fault)
            return [2*math.pi*50*(state[1]-1),(pm-pe-(state[1]-1))/2]
        grid=[float(t) for t in times if left<t<=right]
        # Include a topology boundary even when off the regular sampling grid.
        sample=sorted(set(grid+[right]))
        sol=solve_ivp(rhs,(left,right),x,method='DOP853',rtol=2e-12,atol=1e-13,max_step=.0005,t_eval=sample)
        if not sol.success:raise ValueError('independent swing-equation integration failed')
        states.update(zip(sol.t,sol.y.T.copy()));x=sol.y[:,-1].copy()
    channels={k:[] for k in ['g.delta','g.omega','g.bus_voltage_pu','g.electrical_power_pu']}
    for t in times:
        state=states[float(t)];on=not (.11<=t<.4) if name=='matched_island' else not (opened is not None and t>=opened)
        voltage,pe=electrical(state,on,name!='matched_island' and .1<=t<.4)
        for key,value in zip(channels,[state[0],state[1],abs(voltage)/vbase,pe]):channels[key].append(float(value))
    expected=[]
    if name=='matched_island':expected=[('breaker_open','grid-cb',.11),('breaker_close','grid-cb',.4)]
    elif name in {'overcurrent','manual_inhibit'}:expected=[('network_relay_trip','oc',.12),('breaker_open','line-cb',.13)]
    elif name=='breaker_failure':expected=[('network_relay_trip','oc',.12),('breaker_failure_confirmed','line-cb',.15),('breaker_open','grid-cb',.16)]
    # Under the fault Igrid = a*Egrid + b*Eg. A reverse triangle lower
    # bound proves pickup for every rotor angle, not just sampled trajectory.
    den=1/zg+yl+1/zgrid+1.;a=(1-1/zgrid/den)/zgrid;b=-(1/zg/den)/zgrid
    lower=abs(a)*abs(source)-abs(b)*eabs
    if name not in {'matched_island','measurement_loss'} and lower<=50:raise ValueError('analytic pickup bound does not justify timeline')
    return dict(completed=True,time_s=times.tolist(),channels=channels,channel_inventory=sorted(channels),expected_events=expected,minimum_fault_pickup_bound_a=float(lower),independent_SI_equations=True)


def transfer_reference(name):
    """Standby machine, independent grid/load Thevenin solve and swing RHS.

    Before transfer the disconnected generator produces no terminal current.
    Its zero shaft-power passport then supplies the island from kinetic energy;
    this is deliberately not an invented regulated constant-speed generator.
    """
    success=name=='standby_transfer';vbase=10000/math.sqrt(3);zg=25j;zgrid=2.2+9j;yl=(300000-100000j)/10000**2
    initial_voltage=vbase/(1+zgrid*yl);state=np.array([np.angle(initial_voltage),1.]);times=np.r_[np.arange(120)*.005,.6]
    states={0.:state.copy()};bounds=[0.,.1,.13,.16,.4,.5,.6]
    def switches(t):return (not success or t<.13),success and t>=.16,(0. if .1<=t<.4 else vbase)
    def physical(x,on_grid,on_gen,source):
        eg=vbase*np.exp(1j*x[0]);yg=1/zg if on_gen else 0.;ys=1/zgrid if on_grid else 0.
        voltage=(yg*eg+ys*source)/(yl+yg+ys);current=(eg-voltage)/zg if on_gen else 0j
        return voltage,3*(eg*np.conj(current)).real/1e6
    for left,right in zip(bounds,bounds[1:]):
        on_grid,on_gen,source=switches((left+right)/2)
        def rhs(t,x):
            pe=physical(x,on_grid,on_gen,source)[1]
            return [2*math.pi*50*(x[1]-1),(-pe-(x[1]-1))/2]
        grid=sorted(set([float(t) for t in times if left<t<=right]+[right]))
        sol=solve_ivp(rhs,(left,right),state,method='DOP853',rtol=2e-12,atol=1e-13,max_step=.0005,t_eval=grid)
        if not sol.success:raise ValueError('independent standby transfer integration failed')
        states.update(zip(sol.t,sol.y.T.copy()));state=sol.y[:,-1].copy()
    c={k:[] for k in ['g.delta','g.omega','g.bus_voltage_pu','g.electrical_power_pu','g.connected','source.connected','grid-cb.closed','reserve-cb.closed']}
    for t in times:
        x=states[float(t)];on_grid,on_gen,source=switches(t);v,pe=physical(x,on_grid,on_gen,source)
        for key,value in zip(c,[x[0],x[1],abs(v)/vbase,pe,on_gen,on_grid,on_grid,on_gen]):c[key].append(float(value))
    expected=[('breaker_open','grid-cb',.13),('breaker_close','reserve-cb',.16)] if success else []
    return dict(completed=True,time_s=times.tolist(),channels=c,channel_inventory=sorted(c),expected_events=expected,independent_SI_equations=True,
        expected_attempts=[] if success else [dict(kind='breaker_open',equipment_id='grid-cb',time_s=.13,outcome='failed_to_operate')],
        expected_blocks=[dict(kind='breaker_close',equipment_id='grid-cb',time_s=.5,reason='interlocked_breaker_closed')] if success else
                        [dict(kind='transfer_blocked',equipment_id='ats',time_s=.15,reason='primary_not_open')])


def compare(primary,ref):
    if not primary.get('completed') or not ref.get('completed'):raise ValueError('automation simulation incomplete')
    required={'g.delta','g.omega','g.bus_voltage_pu','g.electrical_power_pu'}
    if not required<=ref.get('channels',{}).keys() or sorted(ref['channels'])!=ref['channel_inventory']:raise ValueError('incomplete reference channel inventory')
    if len(ref['time_s'])<2 or not np.isfinite(ref['time_s']).all() or np.any(np.diff(ref['time_s'])<=0):raise ValueError('invalid reference axis')
    times=np.asarray(primary['time_s']);errors={}
    if not np.isfinite(times).all() or np.any(np.diff(times)<0):raise ValueError('invalid primary axis')
    for key,wanted in ref['channels'].items():
        if key not in primary['channels']:raise ValueError('missing primary channel')
        values=primary['channels'][key];actual=[]
        if len(values)!=len(times) or not np.isfinite(values).all() or len(wanted)!=len(ref['time_s']) or not np.isfinite(wanted).all():raise ValueError('invalid automation channels')
        for t in ref['time_s']:
            indexes=np.flatnonzero(np.isclose(times,t,rtol=0,atol=1e-12))
            if not len(indexes):raise ValueError('missing reference sample')
            actual.append(values[indexes[-1]])
        if not np.isfinite(actual).all():raise ValueError('nonfinite primary')
        errors[key]=float(max(abs(np.array(actual)-wanted)))
    checks=[]
    for kind,id_,at in ref['expected_events']:
        matches=[e for e in primary['event_log'] if e['kind']==kind and e.get('equipment_id')==id_ and (kind not in {'breaker_open','breaker_close'} or e.get('outcome')=='applied')]
        checks.append(len(matches)==1 and abs(matches[0]['time_s']-at)<=LIMITS['event_time_s'])
    if not ref['expected_events']:checks.append(not any(e['kind']=='network_relay_trip' for e in primary['event_log']))
    actual_actions=[(e['kind'],e.get('equipment_id')) for e in primary['event_log'] if
                    e['kind'] in {'network_relay_trip','breaker_failure_confirmed'} or
                    e['kind'] in {'breaker_open','breaker_close'} and e.get('outcome')=='applied']
    checks.append(actual_actions==[(kind,id_) for kind,id_,_ in ref['expected_events']])
    for blocked in ref.get('expected_blocks',[]):
        checks.append(any(e['kind']==blocked['kind'] and e.get('equipment_id')==blocked['equipment_id'] and abs(e['time_s']-blocked['time_s'])<=LIMITS['event_time_s'] and
                          e.get('reason',e.get('synchronism',{}).get('reason'))==blocked['reason'] for e in primary['event_log']))
    for attempt in ref.get('expected_attempts',[]):
        checks.append(any(e['kind']==attempt['kind'] and e.get('equipment_id')==attempt['equipment_id'] and abs(e['time_s']-attempt['time_s'])<=LIMITS['event_time_s'] and e.get('outcome')==attempt['outcome'] for e in primary['event_log']))
    limits={key:LIMITS.get(key.split('.',1)[1],0.) for key in errors}
    return dict(passed=all(checks) and all(error<=limits[key] for key,error in errors.items()),errors=errors,event_checks=checks,limits=limits)


def run(out):
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise ValueError('fresh output directory required')
    code=package_fingerprint();producer=sha256_file(Path(__file__));checks=[]
    write_json(out/'protocol.json',dict(cases={n:example(n).model_dump(mode='json') for n in NAMES},limits=LIMITS,code_sha256=code,producer_sha256=producer))
    for name in NAMES:
        print(name,flush=True);spec=example(name);ref=reference(name,spec);write_json(out/name/'reference.json',ref)
        primary=run_study(spec);write_json(out/name/'primary.json',primary);c=compare(primary,ref);write_json(out/name/'comparison.json',c);checks.append(c)
    if code!=package_fingerprint() or producer!=sha256_file(Path(__file__)):raise ValueError('producer changed during validation')
    result=dict(passed=all(c['passed'] for c in checks),cases=len(checks),code_sha256=code,producer_sha256=producer,
        independent_host=False,OEM_certified=False,artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p.name!='summary.json'})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);a=p.parse_args();r=run(a.out);print(json.dumps(r,indent=2));raise SystemExit(0 if r['passed'] else 1)
