"""Classical mapping vs analytic PF + independent RK45; explicit SMIB fixture."""
from __future__ import annotations
import argparse
import cmath
import json
from pathlib import Path
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.report import write_json
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.rms_study import positive_sequence
from power_grid_env.topology import compile_topology


def run(source: Path, out: Path):
    spec=StudySpec.model_validate_json(source.read_text())
    net=spec.network
    assert len(net.generators)==len(net.sources)==len(net.lines)==len(spec.rms.machines)==1
    assert not net.loads and net.sources[0].angle_a_rad==0
    line=net.lines[0];source_model=net.sources[0];gen=net.generators[0];machine=spec.rms.machines[0]
    z=positive_sequence(np.array(line.r_ohm)+1j*np.array(line.x_ohm))
    assert abs(z.real)<1e-10 and source_model.r_ohm==0 and not np.any(line.b_siemens)
    assert machine.damping_pu==0 and len(spec.rms.events)==1
    event=spec.rms.events[0];assert event.kind=='mechanical_step'
    assert machine.model=='GENCLS'
    rated=machine.rated_va
    base_z=gen.ll_v**2/rated
    x_external=(z.imag+source_model.x_ohm)/base_z
    v_source=source_model.ll_v/gen.ll_v
    pm=gen.p_w/rated
    theta=np.arcsin(pm*x_external/(gen.v_set_pu*v_source))
    terminal=gen.v_set_pu*cmath.exp(1j*theta)
    internal=terminal+1j*machine.x_d_prime_pu*(terminal-v_source)/(1j*x_external)
    delta=cmath.phase(internal)
    rows=[]
    for step in (.01,.005,.0025):
        case=spec.model_copy(deep=True);case.rms.step_s=step
        report=run_study(case)
        assert report['completed'],report
        times=np.array(report['time_s']);expected=np.empty((len(times),2));state=[delta,1.]
        for left,right,p in [(0.,event.time_s,pm),(event.time_s,spec.rms.duration_s,pm+event.value)]:
            def rhs(t,x):
                return [2*np.pi*net.frequency_hz*(x[1]-1),
                        (p-abs(internal)*v_source/(x_external+machine.x_d_prime_pu)*np.sin(x[0]))/(2*machine.inertia_s)]
            solution=solve_ivp(rhs,(left,right),state,rtol=1e-11,atol=1e-12,dense_output=True)
            assert solution.success
            mask=(times>=left)&(times<=right);expected[mask]=solution.sol(times[mask]).T
            state=solution.y[:,-1]
        angle_error=float(np.max(abs(expected[:,0]-report['channels'][gen.id+'.delta'])))
        speed_error=float(np.max(abs(expected[:,1]-report['channels'][gen.id+'.omega'])))
        bus=compile_topology(net).node_to_bus[next(t.connectivity_node_id for t in net.terminals if t.id==gen.terminal_id)]
        pf_angle_error=abs(report['initial']['bus_angle_rad'][bus]-theta)
        rows.append({'step_s':step,'angle_error_rad':angle_error,'speed_error_pu':speed_error,
                     'pf_angle_error_rad':float(pf_angle_error),'init_residual':report['init_residual'],
                     'code_sha256':report['code_sha256'],'network_sha256':report['network_sha256']})
        write_json(out/f'trajectory-{step}.json',report)
    ratios=[rows[i]['angle_error_rad']/rows[i+1]['angle_error_rad'] for i in (0,1)]
    passed=all(row['angle_error_rad']<1e-4 and row['speed_error_pu']<1e-5 and row['pf_angle_error_rad']<1e-6 for row in rows) and all(3.2<r<4.8 for r in ratios)
    result={'verdict':'pass' if passed else 'fail','reference':'analytic lossless SMIB PF and independent scipy RK45',
            'cases':rows,'step_error_ratios':ratios,'industrial_profile_qualified':False,
            'field_validated':False,'scope':'one declared classical SMIB; no regulator/motor qualification'}
    write_json(out/'summary.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('spec',type=Path);parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args();result=run(args.spec,args.out);print(result)
    raise SystemExit(0 if result['verdict']=='pass' else 1)
