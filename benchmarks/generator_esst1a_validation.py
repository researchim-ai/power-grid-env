"""Preregistered loaded-network ESST1A feedback comparison and node-voltage audit."""
from pathlib import Path
import json,math
import numpy as np
from benchmarks.generator_esst1a_cases import NAMES,example
from benchmarks.andes_joint_double_cage import sample_native
from benchmarks.generator_oel2c_validation import PHYSICAL
from benchmarks.esst1a_native import REAL
from power_grid_env.exciter_esst1a import PORTS,VALUES,STATES
from power_grid_env.study_spec import run_study
from power_grid_env.topology import compile_topology


def compare(spec,primary,native):
    if not primary.get('completed') or not native.get('completed'):raise ValueError('complete ESST1A traces required')
    physical=PHYSICAL|{'esst1a_port_set','fault_apply','fault_clear'}
    left=[e for e in primary['event_log'] if e['kind'] in physical];right=[e for e in native['event_log'] if e['kind'] in physical]
    if [(e['kind'],e['equipment_id']) for e in left]!=[(e['kind'],e['equipment_id']) for e in right]:raise ValueError('physical event inventory differs')
    mapping={a['time_s']:b['time_s'] for a,b in zip(left,right)}
    selected=[];times=[];sides=[]
    for i,(t,side) in enumerate(zip(primary['time_s'],primary['sample_side'])):
        grid=abs(t-round(t/spec.coupled.step_s)*spec.coupled.step_s)<1e-12 or abs(t-spec.coupled.duration_s)<1e-12
        if t in mapping and side in ('pre_event','post_event'):selected.append(i);times.append(mapping[t]);sides.append(side)
        elif grid and side!='pre_event':selected.append(i);times.append(t);sides.append('regular')
    errors={};limits={}
    required={p.generator_id+'.ESST1A.'+key for p in spec.coupled.esst1a_exciters for key in (*STATES,*VALUES,*PORTS)}
    required.update(g.id+'.'+key for g in spec.network.generators for key in ('delta','omega','p_w','q_var','current_pu','e1q','e1d','e2d','e2q','psi2d','psi2q','Se','field_pu','TGOV1.valve_pu','TGOV1.turbine_state_pu','TGOV1.output_pu','connected','terminal_voltage_pu'))
    required.update(f.id+'.'+key for f in spec.coupled.faults for key in ('active','current_a','p_w','q_var'))
    required.update(m.id+'.'+key for m in spec.coupled.motors for key in ('speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var','te_pu','connected'))
    from power_grid_env.coupled_pss import STATE_NAMES
    from power_grid_env.excitation_takeover import STATES as OS,VALUES as OV
    required.update(m.generator_id+'.IEEEST.'+k for m in spec.coupled.machines if m.stabilizer for k in [*STATE_NAMES,'sig','raw_pu','limited_pu','output_pu','voltage_gate'])
    required.update(p.generator_id+'.TakeoverOEL.'+k for p in spec.coupled.takeover_oels for k in (*OS,*OV))
    for key in native['channels'].keys()&primary['channels'].keys():
        reference=np.asarray(primary['channels'][key])[selected];measured=sample_native(native,times,sides,key)
        error=float(np.max(abs(reference-measured)))
        if not math.isfinite(error):raise ValueError('nonfinite ESST1A comparison')
        discrete=key.endswith(('_active','.active','.connected','.field_thermal_latched','.field_voltage_enabled','.stator_thermal_latched'))
        errors[key]=error;limits[key]=0. if discrete else 100. if key.endswith(('.p_w','.q_var')) else 2e-5
    if not required<=errors.keys():raise ValueError('missing ESST1A feedback channels: '+str(sorted(required-errors.keys())))
    if set(native['node_voltage'])!={n.id for n in spec.network.connectivity_nodes}:raise ValueError('native node inventory differs')
    buses=compile_topology(spec.network).node_to_bus;levels={v.id:v.nominal_ll_v/math.sqrt(3) for v in spec.network.voltage_levels}
    for node in spec.network.connectivity_nodes:
        bus=buses[node.id];voltage=np.asarray(primary['channels'][bus+'.v'])[selected]*np.exp(1j*np.asarray(primary['channels'][bus+'.a'])[selected])
        for part,expected in [('real_v',voltage.real),('imag_v',voltage.imag)]:
            trace=dict(time_s=native['time_s'],sample_side=native['sample_side'],channels={'v':native['node_voltage'][node.id][part]})
            error=float(np.max(abs(expected-sample_native(trace,times,sides,'v')/levels[node.voltage_level_id])))
            key='node.'+node.id+'.'+part;errors[key]=error;limits[key]=2e-5
    event_error=max((abs(a['time_s']-b['time_s']) for a,b in zip(left,right)),default=0.)
    return dict(passed=all(math.isfinite(v) and v<=limits[k] for k,v in errors.items()) and event_error<=1e-6,errors=errors,limits=limits,event_time_error_s=event_error,samples=len(selected),required_channels=len(required))


def qualify(out,root,names=NAMES,tolerances=(1e-9,1e-10)):
    from benchmarks.esst1a_network import run
    from power_grid_env.evidence import package_fingerprint
    from benchmarks.native_online_validation import source_identity
    from power_grid_env.report import write_json
    if not names or len(set(names))!=len(names) or not set(names)<=set(NAMES):raise ValueError('unique registered ESST1A cases required')
    if not tolerances or any(not math.isfinite(t) or not 0<t<=1e-6 for t in tolerances):raise ValueError('finite positive native tolerances required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity();results=[]
    write_json(out/'summary.json',dict(passed=False,status='running'))
    for name in names:
        spec=example(name);primary=run_study(spec)
        if not primary['completed']:raise ValueError('primary failed: '+name)
        directory=out/name;directory.mkdir();write_json(directory/'primary.json',primary);write_json(directory/'study.json',spec.model_dump(mode='json'))
        for tolerance in tolerances:
            print(name,tolerance,flush=True);native=run(spec,directory/str(tolerance),root,tolerance);check=compare(spec,primary,native)
            write_json(directory/str(tolerance)/'reference.json',native);write_json(directory/str(tolerance)/'comparison.json',check);results.append(dict(case=name,tolerance=tolerance,**check))
    if code!=package_fingerprint() or source!=source_identity():raise ValueError('producer changed during qualification')
    result=dict(passed=all(r['passed'] for r in results),runs=results,code_sha256=code,source_sha256=source,whole_stock_OpenIPSL_network_qualified=False,industrial_profile_qualified=False)
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--cases',nargs='+',choices=NAMES,default=NAMES);args=parser.parse_args()
    result=qualify(args.out,args.root,args.cases);print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
