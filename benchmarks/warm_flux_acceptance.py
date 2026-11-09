"""Independent original motulator flux-coordinate periodic starts and sag.

Native equilibrium is solved from its own zero-flux guess and its own original
RHS. Production states are never used as the native initialization guess.
"""
import argparse
import hashlib
import math
from pathlib import Path
import numpy as np
from scipy.optimize import root
from scipy.integrate import solve_ivp
from power_grid_env.build import NetBuilder
from power_grid_env.motor_catalog import catalog_entry
from power_grid_env.flux_network import FluxNetworkSettings
from power_grid_env.flux_initialization import FluxOperatingPoint,phase_ports
from power_grid_env.phase_network import PhaseCase,run_emt
from power_grid_env.warm_flux import run_warm_flux,verify_warm_flux
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal
from benchmarks.flux_network_native import NativeCircuit,compare,example as network_example
from benchmarks.extended_equipment_validation import verify_native,axis
from benchmarks.motor_transfer_acceptance import phase_case,compare_phase

CASES=('linear_hold','saturated_sag','meshed_pair')


def example(name):
    if name not in CASES:raise ValueError('registered warm case required')
    if name=='meshed_pair':
        n,s=network_example('meshed_pair');s.operating_point=FluxOperatingPoint()
    else:
        p=catalog_entry('aalto-linear-fan' if name=='linear_hold' else 'aalto-saturated-fan')['motor'];p.node_id='motor'
        b=NetBuilder('warm-'+name,'Published motor; authored .17 ohm source and mechanism',.4,s_base_va=5000)
        b.node('motor');b.source('motor',.17,0.)
        n=b.build();s=FluxNetworkSettings(motors=[p],duration_s=.08,step_s=.0005,operating_point=FluxOperatingPoint(),
            events=[] if name=='linear_hold' else [dict(time_s=.025,source_id='source',voltage_pu=.4),dict(time_s=.045,source_id='source',voltage_pu=1.)])
    return n,s


def native_equilibrium(circuit):
    state=circuit.initial();scale=np.ones(len(state));scale[:2*len(circuit.l)]=100.
    electrical=[slice(0,2*len(circuit.l))];speed_indices=[]
    wb=circuit.w
    for p,_,a,_,_ in circuit.entries:
        speed=a+2*(1+len(p.cages));electrical.append(slice(a,speed));scale[speed]=wb/p.pole_pairs;speed_indices.append(speed)
    evaluations=0
    def residual(y):
        nonlocal evaluations
        evaluations+=1
        if evaluations>6000:raise ValueError('native equilibrium evaluation budget')
        x=y*scale;dx=circuit.rhs(0.,x)
        for section in electrical:
            z=x[section][::2]+1j*x[section][1::2];rotation=1j*wb*z
            dx[section.start:section.stop:2]-=rotation.real;dx[section.start+1:section.stop:2]-=rotation.imag
        normalized=dx/(wb*scale)
        for p,_,a,_,_ in circuit.entries:
            speed=a+2*(1+len(p.cages));synchronous=wb/p.pole_pairs
            torque_scale=max(1.,p.fan_nm_per_rad_s2*synchronous**2+p.viscous_nm_per_rad_s*synchronous)
            normalized[speed]=dx[speed]*p.inertia_kg_m2/torque_scale
        return normalized
    for speed in [.99,.95,.85]:
        guess=np.zeros(len(state));guess[speed_indices]=speed
        answer=root(residual,guess,method='hybr',options=dict(xtol=1e-11,maxfev=6000))
        error=float(max(abs(residual(answer.x))))
        if error<=1e-9 and all(.8<=answer.x[i]<=1+1e-9 for i in speed_indices):
            return answer.x*scale,dict(maximum_normalized_residual=error,evaluations=evaluations,initial_guess='own zero flux and high-speed seed; no primary states')
    raise ValueError('native periodic equilibrium failed')


def native_reference(n,s):
    circuit=NativeCircuit(n,s);state,init=native_equilibrium(circuit);times=axis(s);states={0.:state.copy()}
    events={}
    for event in s.events:events.setdefault(event.time_s,[]).append(event)
    for left,right in zip([0.,*sorted(events)],[*sorted(events),s.duration_s]):
        grid=[t for t in times if left<t<=right]
        sol=solve_ivp(circuit.rhs,(left,right),state,method='DOP853',rtol=2e-11,atol=1e-12,max_step=.0001,t_eval=grid)
        if not sol.success or not np.isfinite(sol.y).all():raise ValueError('native warm integration failed')
        states.update(zip(sol.t,sol.y.T.copy()));state=sol.y[:,-1].copy()
        for event in events.get(right,[]):circuit.scales[event.source_id]=event.voltage_pu
    channels={};maximum_kcl=0.
    for t in times:
        circuit.scales={source.id:1. for source in n.sources}
        for event in sorted(s.events,key=lambda e:e.time_s):
            if event.time_s<=t:circuit.scales[event.source_id]=event.voltage_pu
        row,kcl=circuit.outputs(t,states[t]);maximum_kcl=max(maximum_kcl,kcl)
        for key,value in row.items():channels.setdefault(key,[]).append(float(value))
    return dict(completed=True,time_s=times,channels=channels,channel_inventory=sorted(channels),maximum_kcl_relative=maximum_kcl,
        initialization=init,original_motulator_rhs=True,independent_dense_network=True,primary_state_imported=False)


def run(out,wheel):
    wheel=Path(wheel);identity=verify_native(wheel);out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint()
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    def write(name,value):
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(value))
    write('protocol.json',dict(cases=list(CASES),producer_sha256=code,native_sources=identity,benchmark_sources=hashes,
        native_version='0.19.0',native_rtol=2e-11,native_atol=1e-12,native_maximum_step_s=.0001,initialization='independently solved synchronous periodic',phase_case='linear_hold',field_validated=False))
    rows=[]
    for name in CASES:
        n,s=example(name);document=dict(network=n.model_dump(mode='json'),settings=s.model_dump(mode='json'),phase_terminals={'m':('ma','mb','mc')} if name=='linear_hold' else {})
        primary_summary=run_warm_flux(document,out/name/'study');verify_warm_flux(out/name/'study')
        primary=read_json(out/name/'study/result.json');native=native_reference(n,s)
        write(name+'/native.json',native);comparison=compare(primary,native);phase_comparison=None
        if name=='linear_hold':
            phase=phase_case(s.motors[0],n.frequency_hz,400.)
            phase.motors=phase_ports(s,primary['initialization'],{'m':('ma','mb','mc')})
            phase=PhaseCase.model_validate(phase.model_dump(mode='json'))
            phased=run_emt(phase,duration_s=s.duration_s,sample_s=s.step_s,max_step_s=.0001,rtol=2e-9,atol=2e-11)
            phase_comparison=compare_phase(n,primary,phased);write(name+'/phase.json',phase.model_dump(mode='json'));write(name+'/phase-result.json',phased)
        row=dict(id=name,passed=primary_summary['numerically_qualified'] and comparison['passed'] and (phase_comparison is None or phase_comparison['passed']),native=comparison,phase=phase_comparison)
        write(name+'/comparison.json',row);rows.append(row)
    if code!=package_fingerprint() or identity!=verify_native(wheel) or any(hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()!=value for name,value in hashes.items()):raise ValueError('warm producer/reference changed')
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,producer_sha256=code,scope='three finite warm full-winding profiles; independently solved native initial conditions; one explicit phase-network transfer',
        arbitrary_RMS_state_import=False,thermal_equilibrium=False,stability_proven=False,field_validated=False)
    write('summary.json',result);seal(out);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True);parser.add_argument('--wheel',required=True)
    args=parser.parse_args();r=run(args.out,args.wheel);print('passed:',r['passed']);raise SystemExit(0 if r['passed'] else 2)
