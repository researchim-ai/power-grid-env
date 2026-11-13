"""Full recovery/corner qualification with streamed paired-solver evidence."""
import argparse
import time
from pathlib import Path
from power_grid_env.protection_envelope_store import verify_store
from power_grid_env.protection_envelope import effective_plans,ProtectionEnvelope
from power_grid_env.closed_loop_protection import compile_closed_loop,verify_closed_loop,ClosedLoopPlan
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.closed_loop_protection_reference import run as compare


def stable_index(root,identity):
    root=Path(root)
    entries=read_json(root/'index/entries.json')
    for entry in entries:
        if verify(root/'studies'/entry['id'])!=entry['study_manifest_sha256']:
            raise ValueError('study changed during paired qualification')
    if verify(root/'index')!=identity:raise ValueError('store index changed during paired qualification')


def run(radau,dae,owner_radau,owner_dae,out,*,wait_for_studies=False):
    radau=Path(radau);dae=Path(dae);deadline=time.monotonic()+7200
    left=ProtectionEnvelope.model_validate(owner_radau).model_dump(mode='json')
    right=ProtectionEnvelope.model_validate(owner_dae).model_dump(mode='json')
    right['base']['integration_method']='radau'
    if left!=right:raise ValueError('envelopes differ beyond integration method')
    def wait(paths):
        while not all(p.exists() for p in paths):
            if not wait_for_studies or time.monotonic()>deadline:
                raise ValueError('complete sealed study/index required')
            time.sleep(.2)
    root=Path(out);root.mkdir(parents=True,exist_ok=False);rows=[];clock_errors=[0.,0.,0.];event_times=[]
    for name,plan,recipe in effective_plans(left):
        x=radau/'studies'/name;y=dae/'studies'/name
        wait([x/'manifest.json',y/'manifest.json'])
        verify_closed_loop(x,expected_plan=plan)
        other=plan.model_dump(mode='json');other['integration_method']='network_dae'
        verify_closed_loop(y,expected_plan=other)
        result=compare(x,y,root/name)
        rows.append(dict(id=name,passed=result['passed'],grid_pairs=len(result['rows']),reference_manifest_sha256=verify(root/name)))
        if not result['passed']:raise ValueError('paired solver/algebra reference failed: '+name)
        for source in [x,y]:
            verdict=read_json(source/'result.json')
            for action in verdict['actions']:
                for level,grid in enumerate(action['grids']):
                    clock_errors[level]=max(clock_errors[level],*(r['max_progress_error'] for r in grid['clock_audits'].values()))
            report=read_json(source/'studies/fault-primary/grid-2/result.json')
            applied=[e['time_s'] for e in report['event_log'] if e['kind']=='breaker_open' and e.get('equipment_id')=='primary-cb' and e.get('outcome')=='applied']
            if len(applied)!=1:raise ValueError('missing primary clearing event')
            event_times.extend(applied)
        print(name,'paired reference passed',flush=True)
    wait([radau/'index/manifest.json',dae/'index/manifest.json'])
    a=verify_store(radau,expected_envelope=owner_radau);b=verify_store(dae,expected_envelope=owner_dae)
    stable_index(radau,a['index_manifest_sha256']);stable_index(dae,b['index_manifest_sha256'])
    result=dict(passed=a['passes_all_enumerated_corners'] and b['passes_all_enumerated_corners'] and all(r['passed'] for r in rows),
        code_sha256=package_fingerprint(),radau_index_sha256=a['index_manifest_sha256'],dae_index_sha256=b['index_manifest_sha256'],
        rows=rows,physical_grid_runs=a['completed_grid_runs']+b['completed_grid_runs'],
        grid_pairs=sum(r['grid_pairs'] for r in rows),conditional_si_trace_checks=2*sum(r['grid_pairs'] for r in rows),
        max_clock_error_by_grid=clock_errors,primary_clear_time_range_s=[min(event_times),max(event_times)],
        payload_bytes=a['payload_bytes']+b['payload_bytes'],
        continuous_uncertainty_qualified=False,independent_differential_trajectory=False,normative_approval=False,
        producer_sha256=digest(Path(__file__).read_bytes()),
        relay_curves=dict(primary=left['base']['primary']['curve'],backup=left['base']['backup']['curve']),
        scope='all16 finite pickup/time corners of declared relay curves plus nominal; full start/sag/restart and three fault cases on Radau/DAE; independent SI-unit algebra conditioned on saved EMFs')
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--radau',required=True);p.add_argument('--dae',required=True);p.add_argument('--owner-radau',required=True);p.add_argument('--owner-dae',required=True);p.add_argument('--out',required=True);p.add_argument('--wait-for-studies',action='store_true');a=p.parse_args()
    r=run(a.radau,a.dae,read_json(a.owner_radau),read_json(a.owner_dae),a.out,wait_for_studies=a.wait_for_studies)
    print(encoded(dict(passed=r['passed'],runs=r['physical_grid_runs'],pairs=r['grid_pairs'])).decode());raise SystemExit(0 if r['passed'] else 2)
