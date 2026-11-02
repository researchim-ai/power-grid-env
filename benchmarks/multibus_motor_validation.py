"""Three-method acceptance for multisection, multivoltage motor starting."""
from benchmarks.event_sampling import native_sample_sides,native_sample_indices
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.multibus_motor_cases import NAMES
from benchmarks.multibus_si_motor import Circuit
from benchmarks.andes_multibus_motor import native_reference
from benchmarks.native_online_validation import STEPS,channel_limits,match_events,align_event_sides,qualify,source_identity
from benchmarks.andes_joint_double_cage import sample_native
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import sha256_file,write_json
from power_grid_env.topology import compile_topology

ROOT=Path(__file__).resolve().parents[1]
METAMORPHIC_PROTOCOL=dict(base_invariance_maximum_normalized_error=1e-7,
    mesh_minimum_complex_node_voltage_effect_pu=1e-8,clock_tap_minimum_lv_b_complex_voltage_effect_pu=1.,
    references=['production','SI','native_fine'],regular_clock_unchanged=True)


def load_case(name):
    if name not in NAMES:raise ValueError('unknown multibus corpus case')
    return StudySpec.model_validate_json((ROOT/'cases/studies'/('multibus_motor_'+name+'.json')).read_text())


def compare_si(spec,production,independent):
    delta=match_events(production['event_log'],independent['event_log'])
    limits=channel_limits(spec)
    if set(independent['channels'])!=set(limits):raise ValueError('independent multibus channel inventory mismatch')
    errors={key:float(np.max(abs(np.array(production['channels'][key])-value))) for key,value in independent['channels'].items()}
    # DOP853 winding vs Radau EMF: stricter than native step-refinement limits.
    bounds={key:(0. if limit==0 else min(limit,100. if key.endswith(('.p_w','.q_var')) else 1e-6 if key.endswith('timer_remaining_s') else 1e-5)) for key,limit in limits.items()}
    if any(not math.isfinite(v) or v>bounds[k] for k,v in errors.items()) or delta>1e-6:raise ValueError('independent SI multibus comparison exceeded fixed limits')
    return dict(passed=True,maximum_errors=errors,limits=bounds,maximum_event_time_error_s=delta)


def compare_trace(spec,native,production,independent):
    if not native.get('completed'):return dict(completed=False,step_s=native.get('step_s'),failure=native)
    limits=channel_limits(spec)
    if set(native['channels'])!=set(limits):raise ValueError('native multibus channel inventory mismatch')
    event_delta=max(match_events(production['event_log'],native['native_driver_metadata']['event_log']),match_events(independent['event_log'],native['native_driver_metadata']['event_log']))
    times,side_delta=align_event_sides(production,native);errors={};bounds={}
    selected=native_sample_indices(production,(spec.coupled or spec.induction).step_s)
    semantic_sides=native_sample_sides(production)
    times=[times[i] for i in selected];comparison_sides=[semantic_sides[i] for i in selected]
    for key,limit in limits.items():
        values=sample_native(native,times,comparison_sides,key)
        for label,reference in (('production',production),('SI',independent)):
            target=np.array(reference['channels'][key])[selected]
            if target.shape!=values.shape or not np.isfinite(target).all():raise ValueError('invalid multibus comparison channel')
            errors[label+':'+key]=float(np.max(abs(values-target)));bounds[label+':'+key]=limit
    # Production topology is used ONLY to locate exported primary ports.
    # Neither reference network nor native equations receive its stamps.
    ports=compile_topology(spec.network).node_to_bus
    levels={v.id:v.nominal_ll_v/math.sqrt(3) for v in spec.network.voltage_levels}
    if set(native['node_voltage'])!={p.id for p in spec.network.connectivity_nodes}:raise ValueError('missing native node')
    if set(independent['node_voltage'])!=set(native['node_voltage']):raise ValueError('missing SI node')
    for node in spec.network.connectivity_nodes:
        base=levels[node.voltage_level_id];bus=ports[node.id]
        primary=np.array(production['channels'][bus+'.v'])[selected]*np.exp(1j*np.array(production['channels'][bus+'.a'])[selected])
        for component,expected in [('real_v',primary.real),('imag_v',primary.imag)]:
            trace=dict(time_s=native['time_s'],sample_side=native['sample_side'],channels={'v':native['node_voltage'][node.id][component]})
            measured=sample_native(trace,times,comparison_sides,'v')/base
            for label,target in [('production',expected),('SI',np.array(independent['node_voltage'][node.id][component])[selected]/base)]:
                key=label+':node.'+node.id+'.'+component
                errors[key]=float(np.max(abs(measured-target)));bounds[key]=1e-4
    residual=native['maximum_accepted_algebraic_residual_pu']
    if not math.isfinite(residual) or residual>1e-8:raise ValueError('native multibus residual exceeds fixed limit')
    return dict(completed=True,step_s=native['step_s'],errors=errors,limits=bounds,maximum_event_time_error_s=max(event_delta,side_delta),
                maximum_algebraic_residual_pu=residual,native_steps=native['native_steps_executed'],regular_sample_clock_unchanged=True,
                comparison_sampling='export_grid_and_physical_event_sides',
                continuous_subgrid_root_samples_omitted=len(production['time_s'])-len(selected))


def run(out,names=NAMES):
    out=Path(out)
    if not names or len(names)!=len(set(names)) or any(n not in NAMES for n in names):raise ValueError('registered unique multibus cases required')
    if (out/'protocol.json').exists():raise ValueError('use new multibus output directory')
    sources=source_identity();code=package_fingerprint();cases={n:canonical_hash(load_case(n)) for n in names}
    write_json(out/'protocol.json',dict(schema='power-grid-env.multibus-motors/v1',cases=list(names),case_sha256=cases,source_sha256=sources,code_sha256=code,
        native_steps_s=list(STEPS),channel_limits={n:channel_limits(load_case(n)) for n in names},node_complex_voltage_limit_pu=1e-4,
        event_limit_s=1e-6,algebraic_residual_limit_pu=1e-8,primary_port_mapping_only=True,cold_runup_case='runup3s',cold_runup_final_speed_minimum_pu=.95,
        native_branch_input_regularization_offset_pu=1e-8,native_branch_maximum_relative_admittance_error=1e-12,
        SI_limits='zero discrete; min(native,100 W/var) power; min(native,1e-6) timers; min(native,1e-5) others',
        refinement='finest speed/angle errors decrease vs coarse or <=1e-10; every fine channel meets its fixed bound'))
    write_json(out/'metamorphic-protocol.json',METAMORPHIC_PROTOCOL)
    results=[];write_json(out/'summary.json',dict(passed=False,status='running'))
    try:
        for name in names:
            spec=load_case(name);folder=out/name
            if canonical_hash(spec)!=cases[name]:raise ValueError('multibus case changed before integration')
            write_json(folder/'study.json',spec.model_dump(mode='json'));natives=[]
            for h in STEPS:
                print('native',name,h,flush=True);native=native_reference(spec,h)
                write_json(folder/f'andes-{h}.json',native);natives.append(native)
            production=run_study(spec,isolate=True,wall_timeout_s=90.)
            write_json(folder/'production.json',production)
            if not production.get('completed'):raise ValueError('multibus production failed: '+str(production.get('reason')))
            independent=Circuit(spec).run(production);write_json(folder/'SI.json',independent)
            si=compare_si(spec,production,independent);write_json(folder/'SI-comparison.json',si)
            steps=[dict(compare_trace(spec,native,production,independent),step_s=h) for h,native in zip(STEPS,natives)]
            result=dict(case_id=name,steps=steps,**qualify(steps))
            if name=='runup3s':
                result['final_speeds_pu']={label:{m.id:trace['channels'][m.id+'.speed_pu'][-1] for m in spec.induction.motors} for label,trace in [('production',production),('SI',independent),('native_fine',natives[-1])]}
                result['cold_runup_verified']=all(v>.95 for row in result['final_speeds_pu'].values() for v in row.values())
                result['passed']=result['passed'] and result['cold_runup_verified']
            write_json(folder/'comparison.json',result);results.append(result);print('compared',name,result['passed'],flush=True)
            if sources!=source_identity() or code!=package_fingerprint() or any(canonical_hash(load_case(n))!=h for n,h in cases.items()):raise ValueError('multibus producer/cases changed during corpus')
            write_json(out/'summary.json',dict(passed=False,status='running',cases=results))
        summary=dict(passed=all(r['passed'] for r in results),status='complete',cases=results,full_registered_corpus=list(names)==list(NAMES),
                     new_native_runs=3*len(results),fresh_SI_and_production_pairs=len(results),source_sha256=sources,code_sha256=code,
                     controller_commands_precomputed=False,field_validated=False,industrial_profile_qualified=False,independent_host_replay=False)
        summary['artifacts_sha256']={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p!=out/'summary.json'}
        write_json(out/'summary.json',summary);return summary
    except Exception as exc:
        write_json(out/'summary.json',dict(passed=False,status='failed',cases=results,error=str(exc)));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));args=p.parse_args()
    if not run(args.out,args.cases)['passed']:raise SystemExit(1)
