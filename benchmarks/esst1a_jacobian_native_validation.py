"""Fresh-process native OpenIPSL/MSL feedback checks for the NA-04 subset."""
from pathlib import Path
import argparse,json,os,subprocess,sys
from benchmarks.esst1a_jacobian_validation import case
from benchmarks.generator_esst1a_validation import compare
from benchmarks.native_online_validation import source_identity
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.model import canonical_hash

NAMES=('esst_hold','esst_gate_priority','esst_regulator_reset','esst_current_limit','esst_fault_clear','esst_two_exciters',
       'no_uel_pi_overload_recovery','no_uel_pi_pss_limited','no_uel_pi_generator_open','no_uel_pi_authority_limited')


def child(study,out,root,tolerance):
    from benchmarks.esst1a_network import run
    s=StudySpec.model_validate_json(Path(study).read_text());code=package_fingerprint();source=source_identity()
    r=run(s,out,root,tolerance,audit_network=True)
    if code!=package_fingerprint() or source!=source_identity() or r.get('study_sha256')!=canonical_hash(s):raise ValueError('native worker producer/input changed')
    r.update(worker_code_sha256=code,worker_source_sha256=source)
    Path(out,'reference.json').write_text(json.dumps(r,allow_nan=False)+'\n')


def qualify(out,root,*,names=NAMES,case_factory=case):
    from benchmarks.esst1a_native import verify
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    identity=verify(root);code=package_fingerprint();source=source_identity();specs={n:case_factory(n) for n in names}
    protocol=dict(code_sha256=code,source_sha256=source,reference_identity=identity,
                  cases={n:s.model_dump(mode='json') for n,s in specs.items()},native_tolerances=[1e-12,1e-13],worker_timeout_s=360,
                  reference='pinned unmodified OpenIPSL ESST1A; independent SI plant; authored MSL block-graph PI/PSS; no UEL removal by adapter',
                  criteria='existing generator_esst1a_validation.compare, full required states/current/node inventory, 2e-5 pu, 100 W/var, 1us physical events')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');rows=[]
    (out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n')
    try:
        for name,s in specs.items():
            directory=out/name;directory.mkdir();study=directory/'study.json';study.write_text(s.model_dump_json())
            a=run_study(s)
            if not a.get('completed'):raise ValueError('primary failed: '+name)
            (directory/'primary.json').write_text(json.dumps(a,allow_nan=False)+'\n')
            for tolerance in (1e-12,1e-13):
                target=directory/str(tolerance)
                env=os.environ.copy();env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
                args=[sys.executable,'-m','benchmarks.esst1a_jacobian_native_validation','--child','--study',str(study),'--out',str(target),'--root',str(Path(root).resolve()),'--tolerance',str(tolerance)]
                result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=360)
                (directory/(str(tolerance)+'.log')).write_text(result.stdout+'\n'+result.stderr)
                if result.returncode:raise ValueError('native worker failed: '+name+' '+str(tolerance))
                b=json.loads((target/'reference.json').read_text());check=compare(s,a,b)
                if b.get('study_sha256')!=canonical_hash(s) or b.get('worker_code_sha256')!=code or b.get('worker_source_sha256')!=source:raise ValueError('native input/producer mismatch')
                (target/'comparison.json').write_text(json.dumps(check,indent=2)+'\n')
                rows.append(dict(case=name,tolerance=tolerance,**check));print(name,tolerance,check['passed'],flush=True)
                if not check['passed']:raise ValueError('native comparison failed: '+name)
        if package_fingerprint()!=code or source_identity()!=source:raise ValueError('producer changed during native qualification')
        r=dict(passed=True,status='complete',runs=rows,code_sha256=code,source_sha256=source,native_fresh_processes=len(rows),
               whole_stock_OpenIPSL_network_qualified=False,industrial_profile_qualified=False,independent_host_verified=False)
        (out/'summary.json').write_text(json.dumps(r,indent=2)+'\n');return r
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',runs=rows,error=type(exc).__name__+': '+str(exc)),indent=2)+'\n');raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--child',action='store_true');p.add_argument('--study',type=Path);p.add_argument('--tolerance',type=float,default=1e-12);a=p.parse_args()
    if a.child:child(a.study,a.out,a.root,a.tolerance)
    else:qualify(a.out,a.root)


if __name__=='__main__':main()
