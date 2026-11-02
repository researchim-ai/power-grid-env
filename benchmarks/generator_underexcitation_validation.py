"""Preregistered full-network MNLEX2 feedback and independent SI validation."""
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.generator_underexcitation_cases import NAMES,example
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.generator_thermal_validation import full_compare,physical_contract
from benchmarks.native_online_validation import source_identity
from power_grid_env.study_spec import run_study,StudySpec
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json,sha256_file

NEGATIVE=('unknown','duplicate','classical','no_exciter','initial_active','startup_loaded')


def negative(name):
    d=example('hold').model_dump(mode='json');s=d['coupled']
    if name=='unknown':s['underexcitation_limiters'][0]['generator_id']='missing'
    elif name=='duplicate':s['underexcitation_limiters']*=2
    elif name=='classical':s['machines'][1]['model']='GENCLS'
    elif name=='no_exciter':s['machines'][1]['exciter']=None
    elif name=='initial_active':s['underexcitation_limiters'][0]['Radius']=.001
    elif name=='startup_loaded':s['underexcitation_limiters'][0]['initialization']='zero_output_startup'
    else:raise ValueError('unknown negative case')
    return d


def events(report):
    return [(e.get('kind'),e.get('equipment_id'),e.get('controller'),e.get('bound')) for e in report['event_log']]


def compare(spec,primary,si):
    comparison=full_compare(spec,primary,si)
    same=events(primary)==events(si)
    delta=max((abs(a['time_s']-b['time_s']) for a,b in zip(primary['event_log'],si['event_log'])),default=0.) if same else None
    memory_jump=0.;state_jump=0.;resets=0
    for p in spec.coupled.underexcitation_limiters:
        prefix=p.generator_id+'.UEL.'
        for j,side in enumerate(primary['sample_side']):
            if side!='pre_event':continue
            memory_jump=max(memory_jump,abs(primary['channels'][prefix+'feedback_memory_pu'][j+1]-primary['channels'][prefix+'feedback_memory_pu'][j]))
            state_jump=max(state_jump,abs(primary['channels'][p.generator_id+'.e1q'][j+1]-primary['channels'][p.generator_id+'.e1q'][j]))
        resets+=sum(e['kind']=='uel_lag_reset' and e['equipment_id']==p.generator_id for e in primary['event_log'])
    physical=physical_contract(spec,primary);independent_physical=physical_contract(spec,si)
    finite=all(math.isfinite(v) for values in primary['channels'].values() for v in values)
    passed=bool(comparison['passed'] and same and delta<=1e-6 and memory_jump<=1e-10 and state_jump<=1e-10 and physical['passed'] and independent_physical['passed'] and finite)
    return dict(passed=passed,SI=comparison,physical=physical,independent_physical=independent_physical,events_match=same,maximum_event_time_error_s=delta,
                maximum_feedback_memory_jump_pu=memory_jump,maximum_rotor_flux_jump_pu=state_jump,raw_lag_resets=resets)


def run(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity()
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=sources,cases={n:canonical_hash(example(n)) for n in NAMES},negative_inputs={n:negative(n) for n in NEGATIVE},
        SI_state_limit=1e-5,SI_power_limit_w_var=100.,event_time_limit_s=1e-6,state_continuity_limit=1e-10,
        profile='MNLEX2_OpenIPSL_3_1_0',native_network_feedback_runs=0,own_hardware_required=False))
    write_json(out/'summary.json',dict(passed=False,status='running'));checks=[];rejections={}
    for name in NAMES:
        print(name,flush=True);s=example(name);folder=out/name;write_json(folder/'study.json',s.model_dump(mode='json'))
        r=run_study(s,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',r)
        if not r.get('completed'):raise ValueError('primary underexcitation case incomplete: '+name)
        si=independent_circuit(s).run(r);write_json(folder/'SI.json',si)
        check=compare(s,r,si);write_json(folder/'comparison.json',check)
        if not check['passed']:raise ValueError('independent underexcitation comparison failed: '+name)
        checks.append(check)
    for name in NEGATIVE:
        data=negative(name);write_json(out/'negative'/f'{name}-input.json',data)
        try:s=StudySpec.model_validate(data)
        except ValueError:rejections[name]='schema_rejected';continue
        r=run_study(s,isolate=True,wall_timeout_s=90.);write_json(out/'negative'/f'{name}-result.json',r)
        if r.get('completed') or 'channels' in r:raise ValueError('unsupported underexcitation input accepted')
        rejections[name]=r['reason']
    if code!=package_fingerprint() or sources!=source_identity():raise ValueError('producer changed during underexcitation corpus')
    result=dict(passed=True,cases=len(checks),rejected=rejections,maximum_event_time_error_s=max(c['maximum_event_time_error_s'] for c in checks),
        maximum_feedback_memory_jump_pu=max(c['maximum_feedback_memory_jump_pu'] for c in checks),code_sha256=code,native_network_feedback_runs=0,
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*.json') if p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();print(json.dumps(run(a.out),indent=2))
