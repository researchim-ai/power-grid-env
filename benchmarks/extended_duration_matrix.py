"""Sequential fresh-process long meshed RMS acceptance with independent SI MNA.

Resources describe these inputs, not arbitrary scale or solver superiority.
Every row retains failures and requires a complete independent trajectory.
"""
from pathlib import Path
import argparse,json,os,subprocess,sys
from benchmarks.ex03_cases import long_case,scale_case
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.induction import InductionEvent
from power_grid_env.study_spec import StudySpec
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint

NAMES=('grid_mesh_repeated_45s','generator_mesh_repeated_45s','mesh_64_4_1_12s','mesh_256_8_2_12s','large_mesh_512_64_16_12s','large_mesh_2000_128_32_12s','large_mesh_2000_128_32_12s_radau')


def case(name):
    if name not in NAMES:raise ValueError('registered duration matrix case required')
    if name.startswith('large_mesh_'):
        from benchmarks.dae_cases import scale_case as large_case
        nodes,motors,generators=map(int,name.split('_')[2:5])
        s=large_case(nodes,motors,generators,duration_s=12.,voltage_profile='nominal_1p0');s.coupled.step_s=.2
        if name.endswith('_radau'):s.coupled.integration_method='radau'
    elif name.startswith('mesh_'):
        nodes,motors,generators=map(int,name.split('_')[1:4]);s=scale_case(nodes,motors,generators)
        s.coupled.duration_s=12.;s.coupled.step_s=.1
    else:s=long_case('grid_long_start' if name.startswith('grid') else 'gen_long_restart')
    p=s.coupled or s.induction;event=CoupledEvent if s.coupled else InductionEvent
    # The original scenario remains in the protocol. Add a second independent
    # voltage disturbance, leaving all physical motor/regulator parameters fixed.
    for t,v in ((p.duration_s*.65,.7),(p.duration_s*.7,1.)):
        p.events.append(event(time_s=t,kind='source_voltage',equipment_id=s.network.sources[0].id,value=v))
    p.jacobian_format='csc';p.jacobian_network='direct_solve';p.event_policy='coincident_roots_v1'
    p.runtime_limits.maximum_integration_wall_s=600.
    s.id='extended-duration-'+name
    return StudySpec.model_validate(s.model_dump(mode='json'))


def run(out,names=NAMES):
    from benchmarks.ex03_validation import compare
    out=Path(out)
    if out.exists():raise ValueError('fresh duration matrix directory required')
    code=package_fingerprint();rows=[]
    producers={p.name:sha256_file(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
    write_json(out/'protocol.json',dict(cases={n:case(n).model_dump(mode='json') for n in names},code_sha256=code,
        producer_sources_sha256=producers,controller_alignment='distinct TGOV1/SEXS reset rows only, existing 1e-6 s timing gate; no command/protection merging',sequential_fresh_workers=True,reference='independent SI MNA flux-state equations; DOP853',timeout_s=660))
    write_json(out/'summary.json',dict(passed=False,status='running'))
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    for name in names:
        s=case(name);study=out/name/'study.json';write_json(study,s.model_dump(mode='json'));row=dict(case=name,passed=False)
        for role in ('primary','reference'):
            folder=out/name/role
            # Reuse existing audited worker; custom StudySpec is explicit.
            command=[sys.executable,'-c',"from benchmarks.ex03_validation import worker;import sys;worker('grid_long_start',sys.argv[1],sys.argv[2],primary=sys.argv[3],study=sys.argv[4])",role,str(folder),str(out/name/'primary'/'trace.json'),str(study)]
            try:
                r=subprocess.run(command,env=env,capture_output=True,text=True,timeout=660)
                (out/name/(role+'.log')).write_text(r.stdout+r.stderr)
                if r.returncode:raise ValueError(role+' worker failed; see log')
                row[role]=json.loads((folder/'metrics.json').read_text())
            except (subprocess.TimeoutExpired,ValueError) as exc:
                row['failure']=str(exc);break
        else:
            a=json.loads((out/name/'primary'/'trace.json').read_text());b=json.loads((out/name/'reference'/'trace.json').read_text())
            check=compare(s,a,b);write_json(out/name/'comparison.json',check);row['passed']=check['passed']
        rows.append(row);write_json(out/'progress.json',dict(rows=rows));print(name,row['passed'],flush=True)
    if code!=package_fingerprint() or producers!={p.name:sha256_file(p) for p in sorted(Path(__file__).parent.glob('*.py'))}:raise ValueError('producer changed during duration matrix')
    result=dict(passed=all(r['passed'] for r in rows),rows=rows,code_sha256=code,
        multiple_islands_qualified=False,combined_long_limiter_profile_qualified=False,
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='summary.json'})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args()
    raise SystemExit(0 if run(a.out,a.cases)['passed'] else 1)
