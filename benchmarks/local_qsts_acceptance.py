"""Pinned, strictly local SMART-DS day/week acceptance; never downloads data."""
from pathlib import Path
import argparse,hashlib,json
from benchmarks.external import smart_ds_qsts as reference
from benchmarks.release_candidate import source_hashes
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json


def inputs(feeder=None):
    feeder=reference.FEEDER if feeder is None else Path(feeder)
    if not (feeder/'Master.dss').is_file():return {},['missing feeder Master.dss']
    shape=feeder/'LoadShapes.dss'
    if not shape.is_file():return {},['missing LoadShapes.dss']
    profiles=reference._required_profiles(shape)
    errors=[]
    if not profiles:errors.append('no native load profile files declared')
    files=list(feeder.iterdir())+list(profiles.values());inventory={}
    raw=reference.RAW.resolve()
    for path in sorted(set(p.resolve() for p in files)):
        if not path.is_relative_to(raw):errors.append('input escapes raw dataset root');continue
        if not path.is_file():errors.append('missing raw input: '+path.relative_to(raw).as_posix());continue
        inventory[path.relative_to(raw).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return inventory,errors


def qualify(out):
    out=Path(out)
    if out.exists():raise ValueError('new QSTS evidence directory required')
    out.mkdir(parents=True)
    before,errors=inputs();repo=Path(__file__).resolve().parents[1]
    sources=source_hashes(repo);producer=package_fingerprint();results=[]
    write_json(out/'raw-inputs.json',before)
    write_json(out/'source-inputs.json',sources)
    if not errors:
        for steps in (96,672):
            case=out/str(steps);case.mkdir()
            converted=reference.run(case,steps=steps)
            engine=reference.engine_window(steps)
            write_json(case/'engine.json',engine)
            passed=(converted.get('verdict')=='pass' and converted.get('replay',{}).get('verdict')=='pass'
                    and converted.get('window',{}).get('steps')==steps and engine.get('verdict')=='pass'
                    and engine.get('steps')==steps)
            results.append(dict(steps=steps,passed=passed,native_import_voltage_error_pu=converted.get('max_voltage_error_pu'),engine_direct_voltage_error_pu=engine.get('max_voltage_error_pu')))
    after,input_errors=inputs()
    if after!=before or input_errors!=errors:errors.append('raw inputs changed during acceptance')
    if source_hashes(repo)!=sources or package_fingerprint()!=producer:errors.append('producer sources changed during acceptance')
    result=dict(schema='power-grid-env.local-qsts-acceptance/v1',passed=not errors and len(results)==2 and all(r['passed'] for r in results),
        producer_sha256=producer,errors=errors,cases=results,policy='local_only_no_GitHub_no_CI',
        evidence='synthetic public feeder; native OpenDSS vs canonical adapter and engine; same solver family',
        second_external_feeder_qualified=False,field_validated=False,independent_solver_agreement=False,
        limits=['First cached feeder only; no replacement profiles for another feeder','Day/week windows only, not annual qualification'])
    write_json(out/'acceptance.json',result)
    files={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    write_json(out/'evidence-manifest.json',files)
    return result


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=qualify(a.out);print(json.dumps(result));raise SystemExit(0 if result['passed'] else 2)

if __name__=='__main__':main()
