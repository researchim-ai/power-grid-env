"""Independent SI winding/MNA scale reference, streamed at authored samples.

No production topology, motor/generator rates, Jacobians or primary trajectory
is used. The independent PV root and SI differential equations predate the DAE
backend. Capacity is an explicit separate profile; legacy limits stay intact.
"""
from pathlib import Path
import argparse,json,time
import numpy as np
from scipy.integrate import DOP853
from benchmarks.multibus_multi_generator import MultiCircuit
from benchmarks.eight_machine_circuit import EightMachineCircuit
from power_grid_env.study_spec import StudySpec
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from benchmarks.native_online_validation import source_identity


def run(spec,out):
    if spec.coupled.capacity_profile!='extended_128x32' or spec.coupled.faults or any(m.stabilizer for m in spec.coupled.machines) or any([spec.coupled.generator_thermal,spec.coupled.generator_field_thermal,spec.coupled.underexcitation_limiters,spec.coupled.oel2c_limiters,spec.coupled.esst1a_exciters,spec.coupled.takeover_oels]):raise ValueError('declared large SI GENROU/TGOV1/SEXS scope required')
    started=time.monotonic();calls=0;code=package_fingerprint();sources=source_identity()
    plant=MultiCircuit(spec,maximum_duration_s=6.,maximum_nodes=2048,sparse_network=True,maximum_generators=32,maximum_motors=128)
    state=plant.state.copy();channels={};voltages={node:dict(real_pu=[],imag_pu=[]) for node in plant.nodes};times=[];sides=[];audit=[];events=[]
    settings=spec.coupled;commands={}
    for e in settings.events:
        if e.kind not in {'motor_close','motor_open','source_voltage'}:raise ValueError('declared SI scale event inventory required')
        commands.setdefault(e.time_s,[]).append(e)
    grid=np.r_[np.arange(int(np.ceil(settings.duration_s/settings.step_s)))*settings.step_s,settings.duration_s]
    if settings.event_policy=='coincident_roots_v1':
        for at,items in commands.items():commands[at]=sorted(items,key=lambda e:(e.kind in {'motor_open','motor_close'},e.equipment_id,e.kind))
    axis=sorted(set([*grid,*commands]));peak_state_slots=0
    def rhs(t,x):
        nonlocal calls;calls+=1
        if calls>2000000 or time.monotonic()-started>600.:raise ValueError('independent SI scale work budget exceeded')
        return plant.rhs(t,x)
    def record(t,side):
        v,st,rot,torque=plant.electrical(state);times.append(float(t));sides.append(side);audit.append(plant.physical_balance(v,st))
        row={}
        for node,j in plant.nodes.items():voltages[node]['real_pu'].append(float(v[j].real/plant.base[j]));voltages[node]['imag_pu'].append(float(v[j].imag/plant.base[j]))
        for i,(m,p) in enumerate(zip(settings.motors,plant.p)):
            flux=state[p['state']].reshape(-1,2);flux=flux[:,0]+1j*flux[:,1]
            emf=1j*plant.wb*(p['lm']*np.linalg.solve(p['L'],np.ones(len(p['rr']))))@flux/p['vbase'];power=3*v[p['bus']]*np.conj(st[i])
            values=dict(speed_pu=state[p['speed']],e_real_pu=emf.real,e_imag_pu=emf.imag,current_pu=abs(st[i])/p['ibase'],bus_voltage_pu=abs(v[p['bus']])/p['vbase'],p_w=power.real,q_var=power.imag,te_pu=torque[i],connected=float(plant.cfg['connected'][i]))
            values.update(EightMachineCircuit.complete_motor_signals(plant,i,flux,rot));row.update({m.id+'.'+k:float(z) for k,z in values.items()})
        for j,(node,g,at) in enumerate(plant.ports):
            values=g.signals(state[at],plant.generator_currents[j],v[node]);values.update({g.g.id+'.terminal_voltage_pu':float(abs(v[node])/g.vbase),g.g.id+'.connected':1.,g.g.id+'.SEXS.reference_pu':g.vref,g.g.id+'.SEXS.error_pu':g.vref-abs(v[node])/g.vbase})
            row.update(values)
        for key,value in row.items():channels.setdefault(key,[]).append(float(value))
        retained=len(times)*(len(row)+2*plant.N+4)
        if retained>2000000:raise ValueError('independent SI scale retained-output budget exceeded')
    record(0.,'initial')
    for left,right in zip(axis,axis[1:]):
        solver=DOP853(rhs,left,state,right,rtol=2e-11,atol=1e-12,max_step=.005)
        while solver.status=='running':
            solver.step()
            if solver.status=='failed':raise ValueError('independent SI scale integration failed')
        # Public stepper retains only its current RK stages, no dense history.
        peak_state_slots=max(peak_state_slots,solver.K.size+solver.y.size)
        state=solver.y.copy();del solver
        record(right,'pre_event' if right in commands else 'regular')
        if right in commands:
            cfg={k:z.copy() if isinstance(z,np.ndarray) else z for k,z in plant.cfg.items()}
            for e in commands[right]:
                if e.kind=='source_voltage':cfg['scale']=e.value
                else:
                    i=next(i for i,m in enumerate(settings.motors) if m.id==e.equipment_id);cfg['connected'][i]=e.kind=='motor_close'
                events.append(dict(time_s=float(right),kind=e.kind,equipment_id=e.equipment_id))
            plant.set_context(cfg);record(right,'post_event')
    if code!=package_fingerprint() or sources!=source_identity():raise ValueError('independent scale producer changed')
    result=dict(completed=True,study_sha256=canonical_hash(spec),code_sha256=code,source_sha256=sources,time_s=times,sample_side=sides,channels=channels,node_voltage=voltages,network_balance=audit,event_log=events,initialization=plant.initialization,independent_SI_network=True,production_rates_used=False,production_stamps_used=False,reference_rhs_calls=calls,peak_integrator_state_scalar_slots=peak_state_slots,elapsed_s=time.monotonic()-started,scope='declared extended cold double-cage and isotropic GENROU/TGOV1/SEXS; no PSS or thermal relays')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'reference.json').write_text(json.dumps(result,allow_nan=False)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();run(StudySpec.model_validate_json(a.study.read_text()),a.out)
