"""Pinned day/week corpus for two own-profile public feeders, local-only solves."""
from pathlib import Path
import argparse,hashlib,json
from benchmarks.external import smart_ds_qsts as reference
from benchmarks.local_qsts_acceptance import inputs
from benchmarks.release_candidate import source_hashes
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json

SECOND='p1uhs0_1247--p1udt942'


def run(out):
    out=Path(out)
    if out.exists():raise ValueError('new two-feeder evidence directory required')
    out.mkdir(parents=True);repo=Path(__file__).resolve().parents[1];before=source_hashes(repo);code=package_fingerprint()
    feeders=[reference.FEEDER,reference.FEEDER.parent/SECOND];snapshots=[];errors=[];cases=[]
    for feeder in feeders:
        files,issues=inputs(feeder);snapshots.append(files);errors.extend(issues)
        if feeder.name not in {reference.FIRST_LEAF,SECOND}:errors.append('unexpected feeder ID')
    write_json(out/'raw-inputs.json',dict(zip([p.name for p in feeders],snapshots)))
    write_json(out/'sources.json',before)
    if not errors:
        for feeder in feeders:
            for steps in (96,672):
                folder=out/feeder.name/str(steps);folder.mkdir(parents=True)
                converted=reference.run(folder,steps=steps,feeder=feeder);engine=reference.engine_window(steps,feeder=feeder,out=folder)
                write_json(folder/'engine.json',engine)
                passed=converted.get('verdict')=='pass' and converted.get('replay',{}).get('verdict')=='pass' and converted.get('window',{}).get('steps')==steps and converted.get('feeder')==feeder.name and engine.get('verdict')=='pass' and engine.get('steps')==steps and engine.get('restored_post_checkpoint_samples')==steps-steps//2
                cases.append(dict(feeder=feeder.name,steps=steps,passed=passed,native_import_voltage_error_pu=converted.get('max_voltage_error_pu'),engine_direct_voltage_error_pu=engine.get('max_voltage_error_pu')))
                print(feeder.name,steps,passed,flush=True)
    for feeder,original in zip(feeders,snapshots):
        after,issues=inputs(feeder)
        if after!=original:errors.append('raw inputs changed during two-feeder corpus')
    if source_hashes(repo)!=before or package_fingerprint()!=code:errors.append('producer changed')
    result=dict(passed=not errors and len(cases)==4 and all(c['passed'] for c in cases),code_sha256=code,errors=errors,cases=cases,
                second_external_feeder_qualified=not errors and len(cases)==4 and all(c['passed'] for c in cases),
                evidence='two synthetic public SMART-DS feeders with their own original profile files; native OpenDSS versus canonical adapter and Engine',
                independent_solver_agreement=False,field_validated=False,policy='local_only_no_GitHub_no_CI')
    write_json(out/'acceptance.json',result)
    write_json(out/'manifest.json',{p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()})
    return result


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=run(a.out);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)

if __name__=='__main__':main()
