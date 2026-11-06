"""Local, frozen end-to-end acceptance for the three remaining declared profiles.

No remote services. Each worker is a fresh process; the parent seals all inputs,
outputs and code identities. Cached FMUs are verified before use.
"""
from pathlib import Path
import argparse,json,os,subprocess,sys,time,resource
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint


def worker(section,out):
    started=time.monotonic();out=Path(out)
    if section=='sensitivity':
        from benchmarks.full_sensitivity_validation import run
        return run(out)
    out.mkdir(parents=True,exist_ok=False)
    rows=[]
    if section=='protected':
        from benchmarks.protected_motor_cases import NAMES,case
        from benchmarks.protected_motor_reference import reference,compare
        from power_grid_env.protected_motor import run_protected_motor
        from benchmarks.extended_equipment_validation import verify_native
        write_json(out/'native-identity.json',verify_native(Path(os.environ['PGE_MOTULATOR_WHEEL'])))
        for name in NAMES:
            s=case(name);write_json(out/name/'study.json',s.model_dump(mode='json'))
            a=run_protected_motor(s);b=reference(s);check=compare(a,b)
            repeat=run_protected_motor(s)
            deterministic=all(a[k]==repeat[k] for k in ('time_s','channels','event_log','engineering'))
            mode=name.split('__')[1]
            engineering=(a['engineering']['classification']=='accelerated' and a['engineering']['engineering_acceptable']) if mode=='restart' else (a['engineering']['classification']=='protected_trip')
            if mode=='cool_reclose':engineering=engineering and a['engineering']['thermal_trip_count']==2 and any(e['kind']=='relay_reset' and e['outcome']=='applied' for e in a['event_log'])
            for label,data in [('primary',a),('native',b),('comparison',check)]:write_json(out/name/(label+'.json'),data)
            rows.append(dict(case=name,passed=bool(check['passed'] and deterministic and engineering),deterministic=deterministic,engineering_expected=engineering))
            print(section,rows[-1],flush=True)
    elif section.startswith('islands_'):
        from benchmarks.long_islands import case,reference,compare
        from power_grid_env.study_spec import run_study
        mode,variant=section[len('islands_'):].split('__');s=case(mode,variant)
        a=run_study(s);b=reference(variant);check=compare(a,b)
        for label,data in [('study',s.model_dump(mode='json')),('primary',a),('native',b),('comparison',check)]:write_json(out/(label+'.json'),data)
        rows=[dict(case=section,passed=check['passed'])]
    elif section.startswith('joint_'):
        from benchmarks.joint_long_cases import case
        from power_grid_env.study_spec import run_study
        s=case();write_json(out/'study.json',s.model_dump(mode='json'))
        if section=='joint_native':
            from benchmarks.oel2c_model_exchange import run
            from benchmarks.precompiled_joint_reference import PrecompiledJoint
            cache=PrecompiledJoint('data/external/joint-long-fmu','/tmp/pge-fmi-runtime')
            a=run(s,out/'fmi',cache.runtime,1e-12,profile='long_eight_machine_v1',precompiled=cache)
            write_json(out/'native.json',a);rows=[dict(case=section,passed=a.get('completed', bool(a.get('time_s'))))]
        else:
            s.coupled.integration_method=section[len('joint_'):]
            a=run_study(s);write_json(out/'primary.json',a);rows=[dict(case=section,passed=a.get('completed',False))]
    else:raise ValueError('unknown acceptance section')
    result=dict(passed=all(r['passed'] for r in rows),rows=rows,wall_s=time.monotonic()-started,maximum_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    write_json(out/'summary.json',result);return result


def run(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    code=package_fingerprint();sources={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    sections=['sensitivity','protected','islands_radau__matched','islands_network_dae__matched','islands_radau__mismatch','islands_network_dae__mismatch','joint_radau','joint_network_dae','joint_native']
    write_json(out/'protocol.json',dict(sections=sections,code_sha256=code,producer_sources_sha256=sources,independent_host=False,ci=False))
    # Native long run overlaps short studies, each with independent process state.
    jobs=[]
    for section in ['joint_native',*sections[:-1]]:
        log=(out/(section+'.log')).open('w')
        process=subprocess.Popen([sys.executable,'-m','benchmarks.closure_acceptance','--worker',section,'--out',str(out/section)],stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy())
        jobs.append((section,process,log))
    failures=[]
    for section,process,log in jobs:
        rc=process.wait();log.close()
        if rc:failures.append(dict(section=section,exit_code=rc))
    comparisons={}
    if not failures:
        from benchmarks.joint_long_cases import case
        from benchmarks.eight_machine_validation import compare
        s=case();native=json.loads((out/'joint_native/native.json').read_text())
        for mode in ('radau','network_dae'):
            primary=json.loads((out/('joint_'+mode)/'primary.json').read_text());s.coupled.integration_method=mode
            check=compare(s,primary,native,expected_profile='long_eight_machine_v1')
            activation={family:max(abs(v) for k,values in primary['channels'].items() if needle in k for v in values) for family,needle in [('PSS','.IEEEST.output_pu'),('UEL','.UEL.output_pu'),('OEL','.OEL2C.output_pu')]}
            check['activation_maxima']=activation
            check['passed']=bool(check['passed'] and all(v>1e-5 for v in activation.values()))
            comparisons[mode]=check;write_json(out/('joint_'+mode)/'comparison.json',check)
    unchanged=code==package_fingerprint() and sources=={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    result=dict(passed=not failures and unchanged and len(comparisons)==2 and all(c['passed'] for c in comparisons.values()),failures=failures,producer_unchanged=unchanged,code_sha256=code,industrial_release_qualified=False,external_host_verified=False,github_or_ci_used=False,artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*')) if p.is_file()})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--worker');a=p.parse_args()
    result=worker(a.worker,a.out) if a.worker else run(a.out)
    raise SystemExit(0 if result['passed'] else 1)
