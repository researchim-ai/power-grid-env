"""Independent SI nodal equations conditioned on saved motor EMF states.

This is an algebraic/measurement oracle plus analytical definite-time actions,
not an independent differential-equation trajectory or OEM device reference.
"""
import argparse
import cmath
import math
from pathlib import Path
import numpy as np
from power_grid_env.topology import compile_topology
from power_grid_env.closed_loop_protection import verify_closed_loop,compile_closed_loop
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest


def algebra(case,report):
    n=case.study.network;s=case.study.coupled
    top=compile_topology(n);source=n.sources[0];line=n.lines[0]
    upstream=top.terminal_bus[source.terminal_id];downstream=top.node_to_bus[s.motors[0].node_id]
    ll=n.voltage_levels[0].nominal_ll_v;vbase=ll/math.sqrt(3)
    sourcez=complex(source.r_ohm,source.x_ohm);linez=complex(line.r_ohm[0][0],line.x_ohm[0][0])
    load_y=sum(complex(sum(l.p_w.values()),-sum(l.q_var.values()))/ll**2 for l in n.loads)
    motorz=[]
    for m in s.motors:
        xp=m.stator_x_pu+m.rotor_x_pu*m.magnetizing_x_pu/(m.rotor_x_pu+m.magnetizing_x_pu)
        motorz.append(complex(m.stator_r_pu,xp)*m.rated_ll_v**2/m.rated_va)
    ch=report['channels'];errors=dict(voltage_pu=0.,relay_current_a=0.,fault_current_a=0.)
    for i,(t,side) in enumerate(zip(report['time_s'],report['sample_side'],strict=True)):
        scale=1.
        for e in sorted(s.events,key=lambda e:e.time_s):
            if e.kind=='source_voltage' and (t>e.time_s+1e-12 or abs(t-e.time_s)<=1e-12 and side=='post_event'):scale=e.value
        esource=cmath.rect(vbase*scale,source.angle_a_rad)
        ys=ch[source.id+'.connected'][i]/sourcez;yl=ch[line.id+'.closed'][i]/linez
        ym=0j;im=0j
        for m,z in zip(s.motors,motorz):
            y=ch[m.id+'.connected'][i]/z
            emf=complex(ch[m.id+'.e_real_pu'][i],ch[m.id+'.e_imag_pu'][i])*m.rated_ll_v/math.sqrt(3)
            ym+=y;im+=emf*y
        yf=sum(ch[f.id+'.active'][i]/complex(f.r_ohm,f.x_ohm) for f in s.faults)
        matrix=np.array([[ys+yl,-yl],[-yl,yl+ym+load_y+yf]],complex)
        vu,vd=np.linalg.solve(matrix,np.array([ys*esource,im]))
        iline=abs(yl*(vu-vd));isource=abs(ys*(esource-vu))
        for bus,value in [(upstream,vu),(downstream,vd)]:
            actual=cmath.rect(ch[bus+'.v'][i]*vbase,ch[bus+'.a'][i])
            errors['voltage_pu']=max(errors['voltage_pu'],abs(value-actual)/vbase)
        for relay in s.network_automation.relays:
            available=relay.measurement_available
            for e in sorted(s.events,key=lambda e:e.time_s):
                if e.equipment_id==relay.id and e.kind in ['relay_measurement_lost','relay_measurement_restored'] and (t>e.time_s+1e-12 or abs(t-e.time_s)<=1e-12 and side=='post_event'):
                    available=e.kind=='relay_measurement_restored'
            expected=(iline if relay.measurement_kind=='line' else isource) if available else 0.
            errors['relay_current_a']=max(errors['relay_current_a'],abs(expected-ch[relay.id+'.measurement'][i]))
        for f in s.faults:
            expected=abs(vd/complex(f.r_ohm,f.x_ohm))*ch[f.id+'.active'][i]
            errors['fault_current_a']=max(errors['fault_current_a'],abs(expected-ch[f.id+'.current_a'][i]))
    limits=dict(voltage_pu=2e-8,relay_current_a=2e-5,fault_current_a=2e-5)
    return dict(passed=all(errors[k]<=limits[k] for k in limits),errors=errors,limits=limits,samples=len(report['time_s']))


def compare_solvers(a,b):
    # One-sided event samples are compared separately, never interpolated across.
    pairs=[];time_error=0.
    for side in ['initial','regular','pre_event','post_event']:
        left=[i for i,s in enumerate(a['sample_side']) if s==side]
        right=[i for i,s in enumerate(b['sample_side']) if s==side]
        if len(left)!=len(right):return dict(passed=False,reason='one_sided_event_coverage_differs')
        for i,j in zip(left,right):
            error=abs(a['time_s'][i]-b['time_s'][j]);time_error=max(time_error,error)
            if error>1e-7:return dict(passed=False,reason='sample_time_differs',time_error_s=time_error)
            pairs.append((i,j))
    if len(pairs)!=len(a['time_s']) or len(pairs)!=len(b['time_s']):return dict(passed=False,reason='unknown_sample_side')
    keys=[k for k in a['channels'] if k.endswith(('.speed_pu','.current_pu','.v','.progress'))]
    errors={k:max(abs(a['channels'][k][i]-b['channels'][k][j]) for i,j in pairs) for k in keys}
    limits={k:1e-6 if k.endswith('.speed_pu') else 2e-5 for k in keys}
    return dict(passed=all(errors[k]<=limits[k] for k in keys),errors=errors,limits=limits,samples=len(pairs),time_error_s=time_error,time_limit_s=1e-7)


def run(radau,dae,out):
    radau=Path(radau);dae=Path(dae);verify_closed_loop(radau);verify_closed_loop(dae)
    pa,pack,kinds,_=compile_closed_loop(read_json(radau/'plan.json'))
    pb=read_json(dae/'plan.json');pb['integration_method']='radau'
    if pb!=pa.model_dump(mode='json'):raise ValueError('solver studies differ beyond method')
    rows=[]
    for case in pack.cases:
        for level in range(3):
            rel=Path('studies')/case.id/f'grid-{level}'/'result.json'
            a=read_json(radau/rel);b=read_json(dae/rel)
            checks=dict(radau_algebra=algebra(case,a),dae_algebra=algebra(case,b),solver_agreement=compare_solvers(a,b))
            rows.append(dict(case=case.id,grid=level,passed=all(c['passed'] for c in checks.values()),checks=checks))
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    result=dict(passed=all(r['passed'] for r in rows),rows=rows,radau_manifest_sha256=verify(radau),dae_manifest_sha256=verify(dae),
        reference_sha256=digest(Path(__file__).read_bytes()),independent_differential_trajectory=False,
        scope='independent SI nodal algebra conditioned on saved rotor EMFs; analytical action clocks verified by runtime; one-sided Radau/DAE agreement')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--radau',required=True);p.add_argument('--dae',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    r=run(a.radau,a.dae,a.out);print(encoded(dict(passed=r['passed'],comparisons=len(r['rows']))).decode());raise SystemExit(0 if r['passed'] else 2)
