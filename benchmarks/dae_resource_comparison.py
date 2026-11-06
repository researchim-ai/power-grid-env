"""Sequential, alternating fresh-process DAE/Radau resource measurements.

The two pre-existing large mesh cases are shortened to a preregistered 0.25 s
cold-start window at the already registered nominal_1p0 voltage profile. The original
0.995 profile is separately retained as an initialization diagnostic. This is not a long-horizon or mixed-limiter qualification.
"""
import argparse,json,os,subprocess,sys,hashlib
from pathlib import Path
from statistics import median


def run(out,repeats=3):
    from benchmarks.dae_cases import scale_case,SCALES
    from benchmarks.release_candidate import source_hashes
    from power_grid_env.evidence import package_fingerprint
    out=Path(out).resolve()
    if out.exists():raise ValueError('new DAE resource directory required')
    if type(repeats) is not int or not 3<=repeats<=9:raise ValueError('3..9 repeated fresh measurements required')
    out.mkdir(parents=True);repo=Path(__file__).resolve().parents[1];sources=source_hashes(repo);code=package_fingerprint();env=os.environ.copy();env['PYTHONPATH']=str(repo)
    for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:env[key]='1'
    def execute(args,log):
        p=subprocess.run([sys.executable,'-m','benchmarks.dae_validation',*map(str,args)],cwd=repo,env=env,capture_output=True,text=True,timeout=650)
        log.write_text(p.stdout+'\nSTDERR\n'+p.stderr)
        if p.returncode:raise RuntimeError('DAE comparison failed: '+str(log))
    negative=out/'original-0995-diagnostic';negative.mkdir()
    original=scale_case(2000,128,32,duration_s=.25,voltage_profile='original_0995')
    original_path=negative/'study.json';original_path.write_text(original.model_dump_json(indent=2))
    diagnostic=subprocess.run([sys.executable,'-m','benchmarks.dae_validation','--worker','--study',str(original_path),'--mode','radau','--out',str(negative/'radau')],cwd=repo,env=env,capture_output=True,text=True,timeout=650)
    (negative/'worker.log').write_text(diagnostic.stdout+'\nSTDERR\n'+diagnostic.stderr)
    original_result=json.loads((negative/'radau/trace.json').read_text())
    original_diagnostic=dict(completed=original_result.get('completed',False),status=original_result.get('status'),reason=original_result.get('reason'),worker_returncode=diagnostic.returncode)
    results=[]
    for shape in SCALES:
        folder=out/('-'.join(map(str,shape)));folder.mkdir();spec=scale_case(*shape,duration_s=.25,voltage_profile='nominal_1p0')
        study=folder/'study.json';study.write_text(spec.model_dump_json(indent=2));metrics=[];comparisons=[]
        for repeat in range(repeats):
            part=folder/str(repeat);part.mkdir()
            for mode in (['radau','dae'] if repeat%2==0 else ['dae','radau']):
                execute(['--worker','--study',study,'--mode',mode,'--out',part/mode],part/(mode+'.log'))
                row=json.loads((part/mode/'metrics.json').read_text());row['repeat']=repeat;metrics.append(row)
            target=part/'comparison.json';execute(['--compare','--left',part/'radau/trace.json','--right',part/'dae/trace.json','--out',target],part/'compare.log');comparisons.append(json.loads(target.read_text()))
        memory=[]
        for mode in ['radau','dae']:
            execute(['--memory','--study',study,'--mode',mode,'--out',folder/('memory-'+mode)],folder/('memory-'+mode+'.log'))
            memory.append(json.loads((folder/('memory-'+mode)/'metrics.json').read_text()))
        times={m:median(r['wall_s'] for r in metrics if r['mode']==m) for m in ['radau','dae']}
        rss={m:median(r['current_exec_peak_rss_kib_linux'] for r in metrics if r['mode']==m) for m in ['radau','dae']}
        import numpy as np
        product_error=float(np.max(abs(np.load(folder/'memory-radau/product.npy')-np.load(folder/'memory-dae/product.npy'))))
        saved=memory[1]['numpy_tracemalloc_peak_bytes']<memory[0]['numpy_tracemalloc_peak_bytes']
        results.append(dict(nodes=shape[0],motors=shape[1],generators=shape[2],duration_s=.25,metrics=metrics,comparisons= comparisons,
            median_wall_s=times,median_peak_rss_kib_linux=rss,radau_over_dae_wall=times['radau']/times['dae'],
            memory=memory,linear_product_error=product_error,measured_action_memory_advantage=saved,
            measured_runtime_advantage=times['dae']<times['radau'],passed=all(r['passed'] for r in comparisons) and product_error<2e-9))
    result=dict(voltage_profile='nominal_1p0',original_0995_diagnostic=original_diagnostic,passed=all(r['passed'] for r in results) and sources==source_hashes(repo) and code==package_fingerprint(),code_sha256=code,repeats=repeats,cases=results,
                uncontended_by_this_agent=True,parallel_samples=False,scope='registered large meshes, 0.25 s cold-start window; action memory is separate from process RSS',
                broad_runtime_superiority_claimed=False,policy='local_only_no_GitHub_no_CI')
    (out/'comparison.json').write_text(json.dumps(result,indent=2)+'\n');(out/'sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()},indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--repeats',type=int,default=3);a=p.parse_args();r=run(a.out,a.repeats)
    print(json.dumps(dict(passed=r['passed'],cases=[dict(nodes=c['nodes'],speed_ratio=c['radau_over_dae_wall'],action_memory_advantage=c['measured_action_memory_advantage']) for c in r['cases']])));raise SystemExit(0 if r['passed'] else 2)

if __name__=='__main__':main()
