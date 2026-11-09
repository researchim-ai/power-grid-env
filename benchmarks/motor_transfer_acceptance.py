"""Passport referral + cold full-winding/phase/native motulator acceptance.

No RMS/full-winding transient equality is asserted. Two referrals are
representations of one published linear motor, not independent motor families.
"""
import argparse
import hashlib
import math
from pathlib import Path
import numpy as np
from power_grid_env.motor_transfer import transfer_passport,run_transfer,verify_transfer
from power_grid_env.motor_catalog import catalog_entry
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.flux_network import FluxNetworkSettings,run_flux_network
from power_grid_env.phase_network import PhaseCase,run_emt
from power_grid_env.build import NetBuilder
from power_grid_env.topology import compile_topology
from power_grid_env.study_artifacts import encoded,seal
from power_grid_env.evidence import package_fingerprint
from benchmarks.flux_network_native import reference,compare
from benchmarks.extended_equipment_validation import verify_native

CASES=('linear_50hz','linear_60hz','saturated_thermal_phase')
PHASE_LIMITS={'current_alpha_a':3e-4,'current_beta_a':3e-4,'motor_speed_rad_s':3e-4,'torque_nm':3e-4,'voltage_alpha_v':1e-3,'voltage_beta_v':1e-3}


def documents(name):
    if name not in CASES:raise ValueError('registered transfer case required')
    nonlinear=name=='saturated_thermal_phase';f=60. if name=='linear_60hz' else 50.;poles=3 if f==60 else 2
    voltage=690. if f==60 else 400.
    p=catalog_entry('aalto-saturated-viscous-thermal' if nonlinear else 'aalto-linear-fan')['motor']
    p.node_id='motor';p.pole_pairs=poles
    reverse=None
    if not nonlinear:
        reverse=dict(direction='flux_to_rms',frequency_hz=f,pole_pairs=poles,parameter_provenance='Published linear catalog; explicit T split, not identified winding geometry',
            flux=p.model_dump(mode='json'),rated_va=30000. if f==60 else 5000.,rated_ll_v=voltage,stator_leakage_fraction=.04 if f==60 else .02)
    return p,f,voltage,reverse


def phase_case(p,f,voltage):
    result=transfer_passport(dict(direction='flux_to_phase',flux=p.model_dump(mode='json'),frequency_hz=f,pole_pairs=p.pole_pairs,
        phase_nodes=['ma','mb','mc'],parameter_provenance='Same cold Gamma motor; explicit isolated-star a/b/c port'))
    return PhaseCase(id='transfer-phase',frequency_hz=f,nodes=['sa','sb','sc','ma','mb','mc'],
        sources=[dict(id='s'+ph,node='s'+ph,tones=[dict(order=1,rms=voltage/math.sqrt(3),phase_rad=angle)]) for ph,angle in zip('abc',[0.,-2*math.pi/3,2*math.pi/3])],
        resistors=[dict(id='r'+ph,frm='s'+ph,to='m'+ph,resistance_ohm=.17) for ph in 'abc'],
        motors=[result['target']],provenance=dict(network='Authored balanced .17 ohm source, explicit three-wire motor, cold zero currents/fluxes'))


def compare_phase(network,primary,phase):
    if not primary.get('completed') or not phase.get('completed'):raise ValueError('completed trajectories required')
    if len(primary['time_s'])!=len(phase['time_s']) or not np.allclose(primary['time_s'],phase['time_s'],rtol=0,atol=1e-14):raise ValueError('full phase time axes differ')
    errors={}
    for key in ('current_alpha_a','current_beta_a','motor_speed_rad_s','torque_nm'):
        target='speed_rad_s' if key=='motor_speed_rad_s' else key
        a=np.asarray(primary['channels']['m.'+target]);b=np.asarray(phase['channels']['m.'+key])
        if a.shape!=b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('invalid phase motor channel')
        errors[key]=float(max(abs(a-b)))
    abc=np.array([phase['channels']['m'+p+'.voltage_v'] for p in 'abc'])
    space=2/3*np.array([1,np.exp(2j*np.pi/3),np.exp(-2j*np.pi/3)])@abc
    bus=compile_topology(network).node_to_bus['motor']
    for suffix,values in [('alpha',space.real),('beta',space.imag)]:
        errors['voltage_'+suffix+'_v']=float(max(abs(values-np.array(primary['channels'][bus+'.voltage_'+suffix+'_v']))))
    if not all(math.isfinite(v) for v in errors.values()):raise ValueError('nonfinite phase comparison')
    return dict(passed=all(errors[k]<=PHASE_LIMITS[k] for k in errors) and phase['kcl_max_a']<=1e-8 and max(abs(r['residual_j']) for r in phase['energy'])<=1e-5,
        maximum_errors=errors,limits=PHASE_LIMITS,kcl_max_a=phase['kcl_max_a'],maximum_energy_error_j=max(abs(r['residual_j']) for r in phase['energy']),
        scope='same Gamma equations in balanced network and explicit three-wire phase network; not an independent winding implementation')



def t_reference(m,frequency,poles,voltage,times):
    """Independent SI full T-circuit flux equations, not the production RMS RHS."""
    from scipy.integrate import solve_ivp
    wb=2*math.pi*frequency;wm=wb/poles;z=m['rated_ll_v']**2/m['rated_va']
    lm=m['magnetizing_x_pu']*z/wb;ls=m['stator_x_pu']*z/wb;lr=m['rotor_x_pu']*z/wb
    inductance=np.array([[ls+lm,lm],[lm,lr+lm]])
    rs=m['stator_r_pu']*z;rr=m['rotor_r_pu']*z
    inertia=2*m['inertia_s']*m['rated_va']/wm**2
    load=m['mechanical_load'];fan=load['quadratic_torque_pu']*m['rated_va']/wm**3
    visc=load['viscous_torque_pu']*m['rated_va']/wm**2
    def quantities(x):
        psi=np.array([complex(*x[:2]),complex(*x[2:4])]);current=np.linalg.solve(inductance,psi)
        torque=1.5*poles*(current[0]*np.conj(psi[0])).imag
        return psi,current,torque
    def rhs(t,x):
        psi,current,torque=quantities(x);speed=x[4]
        terminal=math.sqrt(2/3)*voltage*np.exp(1j*wb*t)-.17*current[0]
        ds=terminal-rs*current[0];dr=-rr*current[1]+1j*poles*speed*psi[1]
        return [ds.real,ds.imag,dr.real,dr.imag,(torque-fan*speed*abs(speed)-visc*speed)/inertia]
    sol=solve_ivp(rhs,(0.,times[-1]),np.zeros(5),t_eval=times,method='DOP853',rtol=2e-11,atol=1e-12,max_step=.0001)
    if not sol.success or not np.isfinite(sol.y).all():raise ValueError('independent T-circuit integration failed')
    channels={key:[] for key in ('current_alpha_a','current_beta_a','speed_rad_s','torque_nm','stator_flux_alpha_vs','stator_flux_beta_vs')}
    for x in sol.y.T:
        psi,current,torque=quantities(x)
        for key,value in zip(channels,[current[0].real,current[0].imag,x[4],torque,psi[0].real,psi[0].imag]):channels[key].append(float(value))
    return dict(time_s=list(times),channels=channels,scope='independent full T-circuit SI equations; not RMS dynamics')


def compare_t(primary,independent):
    errors={};limits={}
    for key,values in independent['channels'].items():
        error=float(max(abs(np.array(values)-np.array(primary['channels']['m.'+key]))))
        errors[key]=error;limits[key]=3e-6 if 'flux' in key else 3e-4
    return dict(passed=all(math.isfinite(v) and v<=limits[k] for k,v in errors.items()),maximum_errors=errors,limits=limits)


def run(out,wheel):
    wheel=Path(wheel);native_identity=verify_native(wheel);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    code=package_fingerprint();sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    def write(name,value):
        path=out/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encoded(value))
    write('protocol.json',dict(cases=list(CASES),producer_sha256=code,reference_sources=sources,native_sources=native_identity,phase_limits=PHASE_LIMITS,
        initialization='cold_zero',duration_s=.04,step_s=.0005,maximum_reference_step_s=.0001,reference='motulator 0.19.0 original winding RHS + independently authored dense SI network'))
    rows=[]
    for name in CASES:
        p,f,v,reverse=documents(name)
        rms=None
        if reverse:
            result=run_transfer(reverse,out/name/'reverse');verify_transfer(out/name/'reverse');rms=result['target']
            forward=dict(direction='rms_to_flux',rms=result['target'],frequency_hz=f,pole_pairs=p.pole_pairs,parameter_provenance='Explicit roundtrip of linear catalog T representation')
            result=run_transfer(forward,out/name/'forward');verify_transfer(out/name/'forward')
            p=FluxMachinePassport.model_validate(result['target'])
        b=NetBuilder('transfer-'+name,'Authored resistive source; published motor',v/1000,s_base_va=5000,frequency_hz=f)
        b.node('motor');b.source('motor',.17,0.)
        network=b.build();settings=FluxNetworkSettings(motors=[p],duration_s=.04,step_s=.0005)
        phase=phase_case(p,f,v)
        write(name+'/network.json',network.model_dump(mode='json'));write(name+'/settings.json',settings.model_dump(mode='json'));write(name+'/phase.json',phase.model_dump(mode='json'))
        primary=run_flux_network(network,settings);native=reference(network,settings)
        phased=run_emt(phase,duration_s=.04,sample_s=.0005,max_step_s=.0001,rtol=2e-9,atol=2e-11)
        write(name+'/primary.json',primary);write(name+'/native.json',native);write(name+'/phase-result.json',phased)
        external=compare(primary,native);phase_check=compare_phase(network,primary,phased)
        t_check=None
        if rms:
            independent=t_reference(rms,f,p.pole_pairs,v,primary['time_s']);write(name+'/T-reference.json',independent)
            t_check=compare_t(primary,independent)
        row=dict(id=name,passed=external['passed'] and phase_check['passed'] and (t_check is None or t_check['passed']),external=external,phase=phase_check,T_circuit=t_check)
        write(name+'/comparison.json',row);rows.append(row)
    if package_fingerprint()!=code or verify_native(wheel)!=native_identity or any(hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()!=value for name,value in sources.items()):
        raise ValueError('producer/reference changed during acceptance')
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,producer_sha256=code,
        scope='three finite cold 40 ms profiles; one published linear circuit in two representations plus published saturated circuit with authored mechanics/thermal data',
        warm_initialization_validated=False,RMS_transient_equivalence=False,arbitrary_network_transfer=False,field_validated=False)
    write('summary.json',result);seal(out);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True);parser.add_argument('--wheel',required=True)
    args=parser.parse_args();result=run(args.out,args.wheel);print('passed:',result['passed']);raise SystemExit(0 if result['passed'] else 2)
