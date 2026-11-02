"""Verify sealed multibus trajectories and preregistered cross-case invariants."""
from benchmarks.event_sampling import native_sample_sides
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.multibus_motor_validation import load_case
from benchmarks.native_online_validation import channel_limits,align_event_sides
from benchmarks.andes_joint_double_cage import sample_native
from power_grid_env.topology import compile_topology
from power_grid_env.report import sha256_file,write_json
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint

ROOT=Path(__file__).resolve().parents[1]


def check_files(root,manifest):
    root=Path(root).resolve()
    for name,expected in manifest.items():
        path=root/name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('unsafe evidence path: '+name)
        if not path.is_file() or sha256_file(path)!=expected:raise ValueError('evidence integrity mismatch: '+name)


def ports(spec,report,label):
    if label!='production':return report['node_voltage']
    mapping=compile_topology(spec.network).node_to_bus;levels={v.id:v.nominal_ll_v/math.sqrt(3) for v in spec.network.voltage_levels};result={}
    for node in spec.network.connectivity_nodes:
        bus=mapping[node.id];v=np.array(report['channels'][bus+'.v'])*np.exp(1j*np.array(report['channels'][bus+'.a']))*levels[node.voltage_level_id]
        result[node.id]=dict(real_v=v.real.tolist(),imag_v=v.imag.tolist())
    return result


def base_error(a,b,left,right,label):
    physical=a.model_dump(mode='json');other=b.model_dump(mode='json')
    other['id']=physical['id'];other['network']['id']=physical['network']['id'];other['network']['s_base_va']=physical['network']['s_base_va']
    if other!=physical:raise ValueError('base study changed beyond the registered base/identities')
    times,_=align_event_sides(left,right);worst=0.
    ratings={m.id:m.rated_va for m in a.induction.motors}
    for key in channel_limits(a):
        scale=ratings[key.split('.')[0]] if key.endswith(('.p_w','.q_var')) else 1.
        actual=np.array(left['channels'][key]);expected=sample_native(right,times,native_sample_sides(left),key)
        error=float(np.max(abs(actual-expected)))/scale
        if not math.isfinite(error):raise ValueError('nonfinite base-invariance error')
        worst=max(worst,error)
    lp,rp=ports(a,left,label),ports(b,right,label);levels={v.id:v.nominal_ll_v/math.sqrt(3) for v in a.network.voltage_levels}
    for node in a.network.connectivity_nodes:
        for part in ('real_v','imag_v'):
            trace=dict(time_s=right['time_s'],sample_side=right['sample_side'],channels={'v':rp[node.id][part]})
            delta=np.array(lp[node.id][part])-sample_native(trace,times,native_sample_sides(left),'v')
            error=float(np.max(abs(delta)))/levels[node.voltage_level_id]
            if not math.isfinite(error):raise ValueError('nonfinite base-invariance voltage')
            worst=max(worst,error)
    return worst


def initial_port_effect(a,b,left,right,label,node=None):
    lp,rp=ports(a,left,label),ports(b,right,label);levels={v.id:v.nominal_ll_v/math.sqrt(3) for v in a.network.voltage_levels};errors=[]
    for n in a.network.connectivity_nodes:
        if node is not None and n.id!=node:continue
        v=complex(lp[n.id]['real_v'][0],lp[n.id]['imag_v'][0]);w=complex(rp[n.id]['real_v'][0],rp[n.id]['imag_v'][0])
        errors.append(abs(v-w)/levels[n.voltage_level_id])
    if not errors or not np.isfinite(errors).all():raise ValueError('invalid initial voltage effect')
    return float(max(errors))


def audit(root):
    root=Path(root);read=lambda p:json.loads((root/p).read_text())
    summary=read('summary.json');protocol=read('protocol.json');meta=read('metamorphic-protocol.json')
    if not summary['passed'] or not summary['full_registered_corpus'] or len(summary['cases'])!=9:raise ValueError('complete passing multibus corpus required')
    check_files(root,summary['artifacts_sha256']);check_files(ROOT,protocol['source_sha256'])
    if package_fingerprint()!=protocol['code_sha256']:raise ValueError('multibus core revision changed')
    native_sources=[];accepted=0;rollbacks=0;residual=0.
    for name in protocol['cases']:
        spec=load_case(name)
        if canonical_hash(spec)!=protocol['case_sha256'][name]:raise ValueError('multibus study identity changed')
        for h in protocol['native_steps_s']:
            report=read(name+f'/andes-{h}.json')
            if not report['completed'] or report['case_sha256']!=canonical_hash(spec) or report['code_sha256']!=protocol['code_sha256'] or report['step_s']!=h or report['backend_version']!='2.0.0':raise ValueError('native multibus trajectory identity mismatch')
            if not report['stock_generated_cache_unchanged'] or report['controller_commands_precomputed']:raise ValueError('invalid native producer contract')
            if not report['terminal_adaptation']['differential_equations_unchanged'] or not report['terminal_adaptation']['services_unchanged']:raise ValueError('native physical laws changed')
            branch=report['native_branch_coordinates']
            if not branch['physical_impedance_preserved'] or not branch['native_services_unchanged'] or branch['maximum_relative_admittance_error']>1e-12:raise ValueError('native branch coordinate conversion failed')
            native_sources.append(report['native_source_sha256']);accepted+=report['native_steps_executed'];rollbacks+=report['native_driver_metadata']['rollback_count']
            value=report['maximum_accepted_algebraic_residual_pu']
            if not math.isfinite(value) or value>protocol['algebraic_residual_limit_pu']:raise ValueError('native electrical residual exceeded')
            residual=max(residual,value)
    if len(native_sources)!=27 or any(s!=native_sources[0] for s in native_sources):raise ValueError('native backend sources drifted')
    if not math.isfinite(residual) or residual>protocol['algebraic_residual_limit_pu']:raise ValueError('native electrical residual exceeded')
    base=load_case('radial');variant=load_case('base_change');mesh=load_case('mesh');clock=load_case('clock_tap');rows={}
    for label,file in [('production','production.json'),('SI','SI.json'),('native_fine','andes-6.25e-05.json')]:
        left=read('radial/'+file);right=read('base_change/'+file)
        error=base_error(base,variant,left,right,label)
        mesh_effect=initial_port_effect(base,mesh,left,read('mesh/'+file),label)
        clock_effect=initial_port_effect(base,clock,left,read('clock_tap/'+file),label,node='lv_b')
        if error>meta['base_invariance_maximum_normalized_error'] or mesh_effect<meta['mesh_minimum_complex_node_voltage_effect_pu'] or clock_effect<meta['clock_tap_minimum_lv_b_complex_voltage_effect_pu']:raise ValueError('cross-case invariant failed: '+label)
        rows[label]=dict(base_maximum_normalized_error=error,initial_mesh_voltage_effect_pu=mesh_effect,initial_clock_tap_lv_b_effect_pu=clock_effect)
    result=dict(passed=True,verified_native_runs=27,accepted_native_steps=accepted,root_rollbacks=rollbacks,maximum_algebraic_residual_pu=residual,
                identical_native_backend_sources=True,preregistered_cross_case_checks=rows,protocol_sha256=sha256_file(root/'protocol.json'),summary_sha256=sha256_file(root/'summary.json'),
                postprocessing_source_sha256=sha256_file(Path(__file__)),field_validated=False,industrial_profile_qualified=False)
    write_json(root/'audit.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);args=p.parse_args();audit(args.root)
