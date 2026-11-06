"""Installed-package phase/drive/search/surrogate workflow without network access."""
from pathlib import Path
import argparse,json,os,subprocess,hashlib


def run(python,source,out):
    source=Path(source).resolve();out=Path(out).resolve()
    if out.exists():raise ValueError('new acceptance directory required')
    out.mkdir(parents=True);records=[];env=os.environ.copy();env.pop('PYTHONPATH',None);env['PYTHONNOUSERSITE']='1';env['OPENBLAS_NUM_THREADS']='1'
    def cli(*args,expected=0):
        command=[str(python),'-I','-m','power_grid_env.cli','phase-study',*map(str,args)]
        result=subprocess.run(command,cwd=out,env=env,capture_output=True,text=True,timeout=180)
        n=len(records);(out/f'{n:02d}.stdout').write_text(result.stdout);(out/f'{n:02d}.stderr').write_text(result.stderr)
        records.append(dict(argv=command,returncode=result.returncode,expected=expected))
        if result.returncode!=expected:raise ValueError('installed phase CLI failed: '+result.stdout[-1500:]+result.stderr[-1000:])
        return json.loads(result.stdout)
    for name in ['rlc','saturated','four_wire','gfl','gfm','drive','drive_locked','unbalanced_motor']:
        result=cli('run','--study',source/f'cases/studies/phase_{name}.json','--out',out/name)
        if not result['completed'] or abs(result['energy'][-1]['residual_j'])>1e-4:raise ValueError('installed physical energy gate failed')
    cli('initialize','--study',source/'cases/studies/phase_harmonics.json','--out',out/'initialized.json','--duration',.02,'--sample',.0002,'--max-step',.0004)
    cli('run','--study',out/'initialized.json','--out',out/'periodic')
    protocol=source/'cases/studies/phase_search.json'
    cli('search','--protocol',protocol,'--out',out/'part','--max-points',7,expected=2)
    result=cli('search','--protocol',protocol,'--out',out/'full','--resume-from',out/'part')
    if not result['conclusive'] or not result['critical_case_physically_replayed']:raise ValueError('physical scenario search incomplete')
    cli('train',out/'full','--out',out/'model','--max-error',1.)
    query=cli('predict',out/'model','--point','[20,1]','--out',out/'verified')
    if not query['physically_verified'] or not query['accepted']:raise ValueError('prediction lacks physical verification')
    cli('predict',out/'model','--point','[20,1]','--screen-only','--out',out/'screen',expected=2)
    cli('predict',out/'model','--point','[100,1]','--out',out/'outside',expected=2)
    cli('verify',out/'verified');cli('export',out/'verified','--out',out/'result.zip')
    cli('run','--study',source/'cases/studies/phase_rlc.json','--out',out/'rlc',expected=2)
    cancel=out/'cancel';cancel.touch()
    cli('search','--protocol',protocol,'--out',out/'cancelled','--cancel-file',cancel,expected=2)
    cli('search','--protocol',protocol,'--out',out/'after-cancel','--resume-from',out/'cancelled')
    result=dict(passed=True,code_sha256=query['code_sha256'],commands=records,policy='local_only_no_GitHub_no_CI')
    (out/'acceptance.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()},indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--python',type=Path,required=True);p.add_argument('--source',type=Path,default=Path('.'));p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=run(a.python.absolute(),a.source,a.out)
    print(json.dumps(dict(passed=r['passed'],commands=len(r['commands']),code_sha256=r['code_sha256'])))

if __name__=='__main__':main()
