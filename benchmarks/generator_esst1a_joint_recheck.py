"""Re-audit sealed native trajectories without compiling or rerunning physics.

The caller pins the historical delivery hash. Only manifest-verified inputs are
consumed. This is new comparison evidence, never a new native simulation receipt.
"""
from pathlib import Path
import hashlib,json
from benchmarks.generator_esst1a_joint_cases import NAMES,example
from benchmarks.generator_esst1a_joint_validation import compare,expected_rejection
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json
from power_grid_env.study_spec import StudySpec


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recheck(source,out,delivery_sha256):
    source=Path(source).resolve();out=Path(out).resolve()
    if out==source or source in out.parents:raise ValueError('recheck output must be outside historical evidence')
    if len(delivery_sha256)!=64 or any(c not in '0123456789abcdef' for c in delivery_sha256):raise ValueError('explicit SHA256 pin required')
    delivery=source/'delivery.json'
    if digest(delivery)!=delivery_sha256:raise ValueError('historical delivery hash differs')
    manifest=json.loads(delivery.read_text())
    if manifest.get('sealed') is not True or manifest.get('passed') is not True:raise ValueError('sealed passing historical delivery required')
    code=package_fingerprint()
    if manifest.get('production_code_sha256')!=code:raise ValueError('production changed; historical trajectories cannot validate current physics')
    from benchmarks.native_online_validation import source_identity
    producer=source_identity();consumed={}
    def read(relative):
        path=source/relative
        if not path.resolve().is_relative_to(source):raise ValueError('input escapes historical evidence')
        want=manifest.get('artifacts_sha256',{}).get(relative)
        actual=digest(path)
        if want!=actual:raise ValueError('historical artifact hash differs: '+relative)
        consumed[relative]=actual
        return json.loads(path.read_text(),parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON input: '+value)))
    protocol=read('network/protocol.json')
    if set(protocol['cases'])!=set(NAMES):raise ValueError('complete registered joint corpus required')
    tolerances=protocol['native_tolerances']
    if tolerances!=[1e-11,1e-12]:raise ValueError('both qualified native accuracy tiers required')
    if protocol['code_sha256']!=code:raise ValueError('historical protocol production hash differs')
    specs={name:StudySpec.model_validate(protocol['cases'][name]) for name in NAMES}
    if any(specs[name].model_dump()!=example(name).model_dump() for name in NAMES):raise ValueError('registered case passport changed')
    out.mkdir(parents=True,exist_ok=False)
    write_json(out/'summary.json',dict(passed=False,status='running',native_simulations_rerun=False))
    rows=[]
    for name in NAMES:
        primary=read('network/'+name+'/primary.json')
        for tolerance in tolerances:
            native=read('network/'+name+'/'+str(tolerance)+'/reference.json')
            result=compare(specs[name],primary,native)
            accepted=result['passed'] or expected_rejection(name,result)
            write_json(out/(name+'-'+str(tolerance)+'.json'),result)
            rows.append(dict(case=name,tolerance=tolerance,passed=result['passed'],expected_rejection=expected_rejection(name,result),accepted=accepted,reset_checks=len(result['native_UEL_reset_checks']),reset_failures=result['reset_failures']))
    if digest(delivery)!=delivery_sha256 or any(digest(source/p)!=h for p,h in consumed.items()):raise ValueError('historical inputs changed during recheck')
    if code!=package_fingerprint() or producer!=source_identity():raise ValueError('recheck producer changed')
    result=dict(passed=all(r['accepted'] for r in rows),status='complete',native_simulations_rerun=False,production_code_sha256=code,comparison_source_sha256=producer,historical_delivery_sha256=delivery_sha256,consumed_artifacts_sha256=consumed,runs=rows,all_registered_trajectories_agree=all(r['passed'] for r in rows),industrial_profile_qualified=False)
    write_json(out/'summary.json',result)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--delivery-sha256',required=True)
    a=p.parse_args();r=recheck(a.source,a.out,a.delivery_sha256)
    print(json.dumps(dict(passed=r['passed'],runs=len(r['runs']),all_registered_trajectories_agree=r['all_registered_trajectories_agree'],native_simulations_rerun=False)))
    raise SystemExit(0 if r['passed'] else 1)
