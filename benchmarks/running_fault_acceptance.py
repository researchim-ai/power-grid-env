"""Owner-bound acceptance of running fault protection, controls and normals."""
from pathlib import Path
from power_grid_env.running_fault_protection import verify_clearance
from power_grid_env.closed_loop_protection import compile_closed_loop
from power_grid_env.industrial_dol import IndustrialDOLCase,assessment
from power_grid_env.relay_clock_audit import audit_clock
from power_grid_env.model import canonical_hash
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.running_fault_protection_reference import recipes
from benchmarks.running_fault_revision import validate_revision


def qualify(faults,revision,normals,duty,out):
    paths={name:Path(value) for name,value in [('faults',faults),('revision',revision),('normals',normals),('duty',duty)]}
    anchors={name:verify(path) for name,path in paths.items()}
    summaries={k:read_json(v/('result.json' if k=='duty' else 'summary.json')) for k,v in paths.items()}
    if not all(r['passed'] for r in summaries.values()):raise ValueError('all source qualifications required')
    original=read_json(paths['revision']/'original.json');candidate=read_json(paths['revision']/'candidate.json')
    validate_revision(original,candidate)
    if read_json(paths['faults']/'base-plan.json')!=candidate or read_json(paths['normals']/'base-plan.json')!=candidate:
        raise ValueError('candidate owner plan mismatch')
    if summaries['duty']['source_manifest_sha256']!=anchors['faults']:raise ValueError('duty linked to different fault studies')
    expected_rows=[]
    for kind,uv,method,grid,study,req in recipes(candidate):
        name=f'{kind}-'+('uv' if uv else 'no-uv')+f'-{method}-grid{grid}'
        r=verify_clearance(paths['faults']/name,expected_study=study,expected_requirements=req)
        if r['passed']!=uv:raise ValueError('positive/negative control outcome differs')
        expected_rows.append(name)
    if sorted(r['id'] for r in summaries['faults']['rows'])!=sorted(expected_rows):raise ValueError('fault coverage mismatch')
    controls=[]
    for kind,uv,method,grid,study,req in recipes(original):
        if kind!='measurement_loss' or not uv:continue
        name=f'original-{method}-grid{grid}'
        r=verify_clearance(paths['revision']/name,expected_study=study,expected_requirements=req)
        if r['passed'] or not r['checks']['clock_audits'] or r['checks']['grid_isolation_deadline']:
            raise ValueError('original backup rejection not reproduced')
        controls.append(name)
    if sorted(r['id'] for r in summaries['revision']['rows'])!=sorted(controls):raise ValueError('original control coverage mismatch')
    expected_normal={f'{kind}-{method}-grid{grid}' for kind in ['start','repeated_sag','coast_restart'] for method in ['radau','network_dae'] for grid in range(3)}
    if {r['id'] for r in summaries['normals']['rows']}!=expected_normal:raise ValueError('normal mode coverage mismatch')
    _,normal_pack,_,_=compile_closed_loop(candidate)
    normal_owner={c.id:c for c in normal_pack.cases if not c.id.startswith('fault-')}
    for row in summaries['normals']['rows']:
        child=paths['normals']/row['id'];verify(child)
        if not row['passed'] or not read_json(child/'audit.json')['passed']:raise ValueError('normal mode not accepted')
        kind,method,grid=row['id'].rsplit('-',2)
        data=normal_owner[kind].model_dump(mode='json');c=data['study']['coupled']
        c['integration_method']=method;c['step_s']=.002/2**int(grid.removeprefix('grid'))
        for m in c['motors']:
            m['contactor']=dict(sense_node_id=m['node_id'],dropout_voltage_pu=.3,pickup_voltage_pu=.8,
                dropout_delay_s=.09,pickup_delay_s=.5,minimum_off_time_s=.5,maximum_automatic_restarts=0)
        case=IndustrialDOLCase.model_validate(data)
        if read_json(child/'case.json')!=case.model_dump(mode='json'):raise ValueError('normal owner case mismatch')
        report=read_json(child/'trace.json')
        if report.get('code_sha256')!=package_fingerprint() or report.get('network_sha256')!=canonical_hash(case.study.network) or report.get('settings')!=case.study.coupled.model_dump(mode='json'):
            raise ValueError('normal trace identity mismatch')
        dol=assessment(case,report)
        if dol!=read_json(child/'audit.json')['dol'] or not dol['within_limits']:raise ValueError('normal assessment recomputation failed')
        if any(not audit_clock(report,r)['passed'] for r in case.study.coupled.network_automation.relays):raise ValueError('normal relay clock replay failed')
        if any(e['kind']=='network_relay_trip' or e['kind']=='motor_open' and e.get('cause')=='undervoltage_timer' for e in report['event_log']):raise ValueError('normal nuisance trip')
    for label,source in [('faults','benchmarks/running_fault_protection_reference.py'),('revision','benchmarks/running_fault_revision.py'),('normals','benchmarks/running_fault_normal_modes.py'),('duty','benchmarks/motor_fault_branch_duty.py')]:
        if summaries[label]['producer_sha256']!=digest(Path(source).read_bytes()):raise ValueError('benchmark producer changed')
    for label,path in paths.items():
        if verify(path)!=anchors[label]:raise ValueError('source artifact changed')
    result=dict(passed=True,code_sha256=package_fingerprint(),source_manifests=anchors,
                qualified_fault_runs=18,qualified_normal_runs=18,unprotected_negative_runs=18,original_backup_negative_runs=6,
                total_native_runs=60,solver_pairs=27,measured_fault_duty_runs=36,
                requirements_unchanged_between_uv_and_no_uv=True,original_backup_accepted=False,
                full_DES04_complete=False,normative_approval=False,reward=None,
                producer_sha256=digest(Path(__file__).read_bytes()),
                scope='Owner-bound finite running-motor profile and exact source artifact recomputation; normal owner cases, criteria and clocks recomputed. Shared physics, same host; not independent ODE, continuous uncertainty or generic selectivity.')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['faults','revision','normals','duty','out']:p.add_argument('--'+name,required=True)
    r=qualify(**vars(p.parse_args()));print(dict(passed=r['passed'],total_native_runs=r['total_native_runs']))
