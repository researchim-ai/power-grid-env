"""Local-only installed-CLI acceptance: no service, network, publication or CI."""
from pathlib import Path
import argparse,json,os,subprocess,hashlib


def qualify(python,source,out):
    source=Path(source).resolve();out=Path(out).resolve()
    if out.exists():raise ValueError('new evidence directory required')
    out.mkdir(parents=True);commands=[]
    env=os.environ.copy();env.pop('PYTHONPATH',None);env['PYTHONNOUSERSITE']='1';env['OPENBLAS_NUM_THREADS']='1'
    def cli(*args,expected=0):
        command=[str(python),'-m','power_grid_env.cli','workbench',*map(str,args)]
        result=subprocess.run(command,cwd=out,env=env,capture_output=True,text=True,timeout=180)
        index=len(commands);(out/f'{index:02d}.stdout').write_text(result.stdout);(out/f'{index:02d}.stderr').write_text(result.stderr)
        commands.append(dict(argv=command,returncode=result.returncode,expected=expected))
        if result.returncode!=expected:raise ValueError('CLI acceptance failed: '+result.stderr[-1000:]+result.stdout[-1000:])
        return json.loads(result.stdout)
    p=out/'dynamic';q=out/'qsts'
    cli('create',source/'cases/studies/flux-saturated_dol.json','--dir',p)
    baseline=cli('run','baseline','--dir',p);cli('run','repeat','--dir',p)
    assert cli('compare','baseline','repeat','--dir',p,'--out',out/'repeat.json')['passed']
    cli('verify','baseline','--dir',p);cli('export','baseline','--dir',p,'--out',out/'study.zip')
    restored=out/'restored';cli('restore',out/'study.zip','--dir',restored);cli('run','replay','--dir',restored)
    assert cli('compare','imported','replay','--dir',restored,'--out',out/'restored.json')['passed']
    cli('compare','baseline','repeat','--dir',p,'--out',p/'runs/baseline/result.json',expected=2)
    cli('verify','baseline','--dir',p)
    study=json.loads((source/'cases/studies/qsts_runtime.json').read_text());study['scenario']['horizon_s']=180.;study['scenario']['control_interval_s']=60.
    (out/'qsts.json').write_text(json.dumps(study))
    cli('create',out/'qsts.json','--dir',q)
    assert cli('run','part','--dir',q,'--max-steps',1,'--checkpoint-every',1,expected=2)['status']=='paused'
    assert cli('run','continued','--dir',q,'--resume-from','part')['completed']
    cli('run','full','--dir',q)
    assert cli('compare','continued','full','--dir',q,'--out',out/'resumed.json')['passed']
    cli('cancel','--dir',q);cli('run','cancelled','--dir',q,expected=2);cli('clear-cancel','--dir',q)
    payload=dict(passed=True,commands=commands,producer_sha256=baseline['code_sha256'],policy='local_only_no_GitHub_no_CI',installed_python=str(python),scope='bounded flux and QSTS CLI workflows; same-host repeatability')
    (out/'acceptance.json').write_text(json.dumps(payload,indent=2)+'\n')
    files={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'evidence-manifest.json').write_text(json.dumps(files,indent=2)+'\n')
    return payload


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--python',type=Path,required=True);p.add_argument('--source',type=Path,default=Path('.'));p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=qualify(a.python.absolute(),a.source,a.out);print(json.dumps(dict(passed=result['passed'],commands=len(result['commands']),producer_sha256=result['producer_sha256'])))

if __name__=='__main__':main()
