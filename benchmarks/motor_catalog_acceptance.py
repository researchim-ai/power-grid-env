"""Repeated full-winding catalog acceptance against pinned original motulator."""
from pathlib import Path
import argparse
from power_grid_env.motor_catalog import CATALOG_IDS,catalog_entry,catalog_study
from power_grid_env.flux_network import run_flux_network
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from benchmarks.extended_equipment_validation import verify_native
from benchmarks.flux_network_native import reference,compare


def run(out,wheel):
    out=Path(out)
    if out.exists():raise ValueError('fresh catalog acceptance directory required')
    sources=verify_native(wheel);code=package_fingerprint();checks=[]
    producers={p.name:sha256_file(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
    write_json(out/'protocol.json',dict(code_sha256=code,producer_sources_sha256=producers,native_sources=sources,wheel_sha256=sha256_file(wheel),
        passports={k:{**catalog_entry(k),'motor':catalog_entry(k)['motor'].model_dump(mode='json')} for k in CATALOG_IDS},
        cases={k:dict(network=catalog_study(k)[0].model_dump(mode='json'),settings=catalog_study(k)[1].model_dump(mode='json')) for k in CATALOG_IDS}))
    for identifier in CATALOG_IDS:
        network,settings=catalog_study(identifier);native=reference(network,settings,max_step=.0005)
        write_json(out/identifier/'native.json',native);previous=None
        for repeat in range(2):
            primary=run_flux_network(network,settings);check=compare(primary,native)
            if previous is not None:
                check['repeat_equal']=all(primary[k]==previous[k] for k in ('time_s','channels','sample_side'))
                check['passed'] &= check['repeat_equal']
            previous=primary
            write_json(out/identifier/str(repeat)/'primary.json',primary);write_json(out/identifier/str(repeat)/'comparison.json',check)
            checks.append(check);print(identifier,repeat,check['passed'],flush=True)
    if code!=package_fingerprint() or producers!={p.name:sha256_file(p) for p in sorted(Path(__file__).parent.glob('*.py'))}:raise ValueError('producer changed during acceptance')
    result=dict(passed=all(c['passed'] for c in checks),comparisons=len(checks),code_sha256=code,
        measured_hardware=False,calibrated_thermal_protection=False,
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json'))})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--wheel',type=Path,required=True);a=p.parse_args()
    raise SystemExit(0 if run(a.out,a.wheel)['passed'] else 1)
