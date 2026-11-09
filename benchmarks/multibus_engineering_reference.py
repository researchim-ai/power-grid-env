"""Independent SI winding/network checks for a completed engineering packet.

This reuses the authored independent circuit, not another external simulator.
Production contributes input passports and requested sample times, not states
or precomputed controller commands. Limits are the existing fixed SI limits.
"""
from pathlib import Path
import argparse
import hashlib

from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.multibus_motor_validation import compare_si
from power_grid_env.motor_engineering import verify_engineering
from power_grid_env.study_spec import StudySpec
from power_grid_env.study_artifacts import read_json, encoded, seal
from power_grid_env.evidence import package_fingerprint


def run(packet, out):
    packet=Path(packet);out=Path(out)
    identity=verify_engineering(packet)
    source_files=[Path(__file__),*Path(__file__).parent.glob('*.py')]
    sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(source_files))}
    producer=package_fingerprint()
    out.mkdir(parents=True,exist_ok=False)
    plan=read_json(packet/'input.json');rows=[]
    protocol=dict(schema='power-grid-env.multibus-engineering-si/v1',packet_manifest_sha256=identity['manifest_sha256'],
        producer_sha256=producer,reference_sources=sources,case_ids=[c['id'] for c in plan['cases']],
        reference_kind='authored_independent_equations',external_simulator=False,field_validated=False,
        scope='finest grid full-horizon SI winding/network trajectory and event comparison; fixed existing SI limits')
    (out/'protocol.json').write_bytes(encoded(protocol))
    for case in plan['cases']:
        folder=packet/case['id']/'grid-2'
        spec=StudySpec.model_validate(read_json(folder/'study.json'))
        primary=read_json(folder/'result.json')
        independent=independent_circuit(spec).run(primary)
        result=compare_si(spec,primary,independent)
        (out/(case['id']+'-si.json')).write_bytes(encoded(independent))
        rows.append(dict(id=case['id'],**result))
    if package_fingerprint()!=producer or any(hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()!=value for name,value in sources.items()):
        raise ValueError('producer/reference changed during comparison')
    if verify_engineering(packet)['manifest_sha256']!=identity['manifest_sha256']:
        raise ValueError('engineering packet changed during comparison')
    summary=dict(passed=all(r['passed'] for r in rows),cases=rows,**protocol)
    (out/'summary.json').write_bytes(encoded(summary));seal(out)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    result=run(args.packet,args.out)
    print('passed:',result['passed'],'cases:',len(result['cases']))
