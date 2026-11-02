"""Three-method, three-step qualification of DOL/dip/coast/protected restart."""
from pathlib import Path
import argparse,json
from benchmarks.multibus_restart_cases import NAMES
from benchmarks.multibus_restart_audit import LIMITS,contract
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.multibus_motor_validation import compare_si,compare_trace
from benchmarks.andes_multibus_motor import native_reference
from benchmarks.native_online_validation import STEPS,source_identity,qualify,channel_limits
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint

ROOT=Path(__file__).resolve().parents[1]


def load_case(name):
    if name not in NAMES:raise ValueError('registered restart case required')
    return StudySpec.model_validate_json((ROOT/'cases/studies'/('multibus_restart_'+name+'.json')).read_text())


def run(out,names=NAMES):
    out=Path(out)
    if (out/'protocol.json').exists():raise ValueError('fresh restart output directory required')
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('unique registered restart cases required')
    sources=source_identity();code=package_fingerprint();hashes={n:canonical_hash(load_case(n)) for n in names}
    write_json(out/'protocol.json',dict(schema='power-grid-env.multibus-restart/v1',cases=list(names),case_sha256=hashes,source_sha256=sources,code_sha256=code,native_steps_s=list(STEPS),native_timeout_s=300.,physical_and_protection_limits=LIMITS,channel_limits={n:channel_limits(load_case(n)) for n in names},event_time_limit_s=1e-6,initial_generator_rhs_limit=1e-8,initial_generator_Q_agreement_limit_var=.01,minimum_before_dip_and_recovered_speed_pu=.95,thermal_case='m intentionally trips during initial DOL; other motors must complete start and restart',refinement='Every fine channel within fixed limits; speed/angle fine errors decrease vs coarse or <=1e-10'))
    rows=[];write_json(out/'summary.json',dict(passed=False,status='running'))
    try:
        for name in names:
            spec=load_case(name);folder=out/name;write_json(folder/'study.json',spec.model_dump(mode='json'));natives=[]
            for h in STEPS:
                print('native',name,h,flush=True);native=native_reference(spec,h,timeout_s=300.);write_json(folder/f'andes-{h}.json',native)
                if not native.get('completed'):raise ValueError('incomplete native restart: '+str(native.get('log_tail',native.get('status'))))
                initial=native['initialization']
                if initial['maximum_generator_initial_rhs']>1e-8 or not initial['generator_states_preserved_during_motor_replacement']:raise ValueError('native prestart initialization failed')
                if not native['stock_generated_cache_unchanged'] or native['controller_commands_precomputed']:raise ValueError('native restart provenance failed')
                natives.append(native)
            primary=run_study(spec,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',primary)
            if not primary.get('completed'):raise ValueError('primary restart failed: '+str(primary.get('reason')))
            si=independent_circuit(spec).run(primary);write_json(folder/'SI.json',si);write_json(folder/'SI-comparison.json',compare_si(spec,primary,si))
            steps=[compare_trace(spec,n,primary,si) for n in natives];refinement=qualify(steps)
            contracts={label:contract(name,spec,trace,native=label.startswith('native')) for label,trace in [('primary',primary),('SI',si),*[(f'native-{h}',n) for h,n in zip(STEPS,natives)]]}
            q_errors=[abs(n['initialization']['generator_initial_q_var']-si['initialization']['generator_initial_q_var']) for n in natives] if spec.coupled else []
            passed=refinement['passed'] and all(c['passed'] for c in contracts.values()) and all(e<=.01 for e in q_errors)
            row=dict(case_id=name,passed=bool(passed),steps=steps,refinement=refinement,contracts=contracts,initial_Q_errors_var=q_errors)
            write_json(folder/'comparison.json',row);rows.append(row);print('compared',name,row['passed'],flush=True)
            if sources!=source_identity() or code!=package_fingerprint() or any(canonical_hash(load_case(n))!=h for n,h in hashes.items()):raise ValueError('restart corpus producer changed')
            write_json(out/'summary.json',dict(passed=False,status='running',cases=rows))
        summary=dict(passed=all(r['passed'] for r in rows),status='complete',full_registered_corpus=list(names)==list(NAMES),cases=rows,native_runs=3*len(rows),fresh_primary_SI_pairs=len(rows),source_sha256=sources,code_sha256=code,field_validated=False,industrial_profile_qualified=False,independent_host=False)
        summary['artifacts_sha256']={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p!=out/'summary.json'}
        write_json(out/'summary.json',summary);return summary
    except Exception as exc:
        write_json(out/'summary.json',dict(passed=False,status='failed',cases=rows,error=str(exc)));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args()
    if not run(a.out,a.cases)['passed']:raise SystemExit(1)
