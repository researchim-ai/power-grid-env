"""Registered finite static balancing qualification, pinned data, no downloads."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from power_grid_env.ac_dispatch import run_dispatch,verify_dispatch
from power_grid_env.balanced_ac import case_from_document
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from benchmarks.ac_dispatch_reference import solve_reference
from benchmarks.ieee_ac_comparison import independent_balance,normalize_primary
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'cases/ac-dispatch'


def score(case,primary,reference):
    native=normalize_primary(case,dict(primary,iterations=0));ref=reference['result'];errors={}
    for key in ['voltage_pu','angle_rad','branch_power_mva']:
        a=np.array(native[key]);b=np.array(ref[key]);
        if a.shape!=b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('reference inventory or finite state differs')
        errors[key]=float(np.max(abs(a-b),initial=0.))
    # Normalize to the original reference for the independent direct checker.
    ref=dict(ref,angle_rad=(np.array(ref['angle_rad'])-ref['angle_rad'][next(i for i,b in enumerate(case['buses']) if b['type_code']==3)]).tolist())
    errors['angle_rad']=float(np.max(abs(np.array(native['angle_rad'])-ref['angle_rad']),initial=0.))
    errors['pg_mw']=float(np.max(abs(np.array(primary['pg_mw'])-reference['result']['pg_mw']),initial=0.))
    errors['q_by_bus_mvar']=max((abs(a[2]-b[2]) for a,b in zip(native['generation_by_bus'],ref['generation_by_bus'],strict=True)),default=0.)
    balance=dict(primary=independent_balance(case,native),reference=independent_balance(case,ref))
    return dict(errors=errors,independent_balance_mva=balance,passed=bool(errors['voltage_pu']<=1e-8 and errors['angle_rad']<=1e-8 and errors['branch_power_mva']<=1e-5 and errors['pg_mw']<=1e-6 and errors['q_by_bus_mvar']<=1e-5 and max(balance.values())<=1e-6 and reference['maximum_residual_pu']<=1e-9))


def inputs():return sorted(p for p in DATA.glob('*.json') if p.name!='origin.json')


def hashes():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(__file__).with_name('ac_dispatch_reference.py'),*inputs()]}


def qualify(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=hashes();rows=[]
    for path in inputs():
        d=read_json(path);r=run_dispatch(d,out/path.stem);verify_dispatch(out/path.stem)
        if not r['completed']:rows.append(dict(case=path.stem,passed=False,reason=r['reason']));continue
        ref=solve_reference(case_from_document(d['case']),d['options']);comparison=score(d['case'],r['result'],ref)
        (out/(path.stem+'-reference.json')).write_bytes(encoded(ref))
        row=dict(case=path.stem,**comparison,former_slack_q_mode=r['result']['q_modes'][str(next(b['id'] for b in d['case']['buses'] if b['type_code']==3))],p_modes=r['result']['p_modes']);rows.append(row);print(json.dumps(row),flush=True)
    s=dict(schema='pge-dispatch-qualification/v1',producer_sha256=code,source_hashes=sources,rows=rows,passed=all(r['passed'] for r in rows) and package_fingerprint()==code and hashes()==sources,
        reference='PP 3.5.6 makeYbus/Yf/Yt + SciPy hybr numerical Jacobian; own residual and cold initial guess; same declared static control equations',
        pandapower_runpp_executed=False,frequency_dynamics=False,automatic_islanding=False,stability_proven=False)
    (out/'summary.json').write_bytes(encoded(s));seal(out);return s


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=hashes():raise ValueError('current dispatch producers and registered inputs required')
    for path in inputs():
        d=read_json(path);folder=out/path.stem;verify_dispatch(folder);r=read_json(folder/'result.json')
        if read_json(folder/'input.json')!=d:raise ValueError('registered input differs')
        if not r['completed']:rows.append(dict(case=path.stem,passed=False,reason=r['reason']));continue
        ref=read_json(out/(path.stem+'-reference.json'));comparison=score(d['case'],r['result'],ref)
        rows.append(dict(case=path.stem,**comparison,former_slack_q_mode=r['result']['q_modes'][str(next(b['id'] for b in d['case']['buses'] if b['type_code']==3))],p_modes=r['result']['p_modes']))
    if s['rows']!=rows or s['passed']!=all(x['passed'] for x in rows):raise ValueError('dispatch qualification conclusion differs')
    if any(s[k] for k in ['pandapower_runpp_executed','frequency_dynamics','automatic_islanding','stability_proven']):raise ValueError('unsupported reference qualification claim')
    return dict(verified=True,passed=s['passed'],cases=len(rows),manifest_sha256=identity,scope='stored constrained dispatch states and independent terminal balances/comparisons; no solver replay or authentication')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args();r=qualify(a.out) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)
