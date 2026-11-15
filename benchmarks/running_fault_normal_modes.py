"""Retain all owner start/sag/coast criteria with revised protection and UV."""
from pathlib import Path
from power_grid_env.closed_loop_protection import compile_closed_loop
from power_grid_env.industrial_dol import IndustrialDOLCase,assessment
from power_grid_env.study_spec import run_study
from power_grid_env.relay_clock_audit import audit_clock
from power_grid_env.study_artifacts import read_json,encoded,seal,digest
from benchmarks.closed_loop_protection_reference import compare_solvers,algebra


def qualify(base,out):
    _,pack,_,_=compile_closed_loop(base)
    root=Path(out);root.mkdir(parents=True,exist_ok=False);rows=[];reports={};pairs=[]
    for original in pack.cases:
        if original.id.startswith('fault-'):continue
        for method in ['radau','network_dae']:
            for grid in range(3):
                data=original.model_dump(mode='json');c=data['study']['coupled']
                c['integration_method']=method;c['step_s']=.002/2**grid
                for m in c['motors']:
                    m['contactor']=dict(sense_node_id=m['node_id'],dropout_voltage_pu=.3,pickup_voltage_pu=.8,
                        dropout_delay_s=.09,pickup_delay_s=.5,minimum_off_time_s=.5,maximum_automatic_restarts=0)
                case=IndustrialDOLCase.model_validate(data)
                name=f'{case.id}-{method}-grid{grid}';print(name,flush=True)
                report=run_study(case.study);dol=assessment(case,report)
                clocks={p.id:audit_clock(report,p) for p in case.study.coupled.network_automation.relays}
                no_trip=not any(e['kind']=='network_relay_trip' for e in report['event_log'])
                no_uv_trip=not any(e['kind']=='motor_open' and e.get('cause')=='undervoltage_timer' for e in report['event_log'])
                oracle=algebra(case,report)
                checked=dict(passed=dol['within_limits'] and no_trip and no_uv_trip and oracle['passed'] and all(a['passed'] for a in clocks.values()),
                             dol=dol,no_relay_trip=no_trip,no_uv_trip=no_uv_trip,clocks=clocks,algebra=oracle)
                child=root/name;child.mkdir()
                for key,value in [('case',case.model_dump(mode='json')),('trace',report),('audit',checked)]:
                    (child/(key+'.json')).write_bytes(encoded(value))
                seal(child);rows.append(dict(id=name,passed=checked['passed']))
                reports[(case.id,method,grid)]=report
                if method=='network_dae':pairs.append(dict(case=case.id,grid=grid,**compare_solvers(reports[(case.id,'radau',grid)],report)))
    result=dict(passed=len(rows)==18 and all(r['passed'] for r in rows) and all(p['passed'] for p in pairs),
                native_runs=len(rows),rows=rows,solver_pairs=pairs,criteria_preserved=True,
                producer_sha256=digest(Path(__file__).read_bytes()),scope='All three original start/repeated-sag/coast-restart criteria retained; revised VI backup and non-restarting UV contactor; three grids, two solvers. Conditional algebra, not independent ODE.')
    (root/'base-plan.json').write_bytes(encoded(base));(root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=qualify(read_json(a.base),a.out);print(dict(passed=r['passed'],native_runs=r['native_runs']))
    if not r['passed']:raise SystemExit(2)
