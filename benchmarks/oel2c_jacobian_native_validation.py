"""Preregistered OEL2C continuous-network checks against pinned native FMUs."""
from pathlib import Path
import argparse,json
from benchmarks.oel2c_jacobian_validation import case
from benchmarks.generator_oel2c_validation import native_reference,compare
from benchmarks.native_online_validation import source_identity
from benchmarks.dynawo_reference_setup import verify
from power_grid_env.study_spec import run_study
from power_grid_env.evidence import package_fingerprint

NAMES=('cold__inverse_ramp','cold__field_trip','loaded__UEL_coordination','loaded__zero_enable_delay',
       'active__voltage_down','active__leadlag2_authority','regulating__voltage_up','regulating__scaled_input',
       'selected__timer_lower_contact','selected__zero_error_voltage_down')
TOLERANCES=(1e-12,1e-13)


def qualify(out,root,*,names=NAMES,case_factory=case):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity();identity=verify(root)
    specs={n:case_factory(n) for n in names}
    (out/'protocol.json').write_text(json.dumps(dict(code_sha256=code,source_sha256=sources,reference_identity=identity,
        cases={n:s.model_dump(mode='json') for n,s in specs.items()},native_tolerances=TOLERANCES,worker_timeout_s=300,
        comparison='existing generator_oel2c_validation.compare; required channels, physical event sides and balances; internal limiter clocks separately qualified',
        reference='pinned Dynawo-derived explicitly named cap-guard-v1/equilibrium-guard-v2 OEL2C, MSL 3.2.3; independent SI network; not whole stock Dynawo'),indent=2)+'\n')
    rows=[];(out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n')
    try:
        for name,s in specs.items():
            folder=out/name;folder.mkdir();(folder/'study.json').write_text(s.model_dump_json())
            a=run_study(s);(folder/'primary.json').write_text(json.dumps(a,allow_nan=False)+'\n')
            if not a.get('completed'):raise ValueError('primary incomplete: '+name)
            for tolerance in TOLERANCES:
                b=native_reference(s,folder/str(tolerance),root,tolerance);check=compare(s,a,b)
                (folder/(str(tolerance)+'-comparison.json')).write_text(json.dumps(check,indent=2)+'\n')
                rows.append(dict(case=name,tolerance=tolerance,**check));print(name,tolerance,check['passed'],flush=True)
                if not check['passed']:raise ValueError('native comparison failed: '+name)
        if code!=package_fingerprint() or sources!=source_identity() or identity!=verify(root):raise ValueError('producer/reference changed during native qualification')
        result=dict(passed=True,status='complete',runs=rows,code_sha256=code,source_sha256=sources,native_fresh_processes=len(rows),
                    whole_stock_Dynawo_network_qualified=False,industrial_profile_qualified=False,independent_host_verified=False)
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',runs=rows,error=type(exc).__name__+': '+str(exc)),indent=2)+'\n');raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args();qualify(a.out,a.root)
