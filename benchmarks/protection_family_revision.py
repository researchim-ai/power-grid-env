"""Explicit EI setting revision with unchanged acceptance requirements."""
import argparse
import copy
from pathlib import Path
from power_grid_env.protection_envelope import ProtectionEnvelope,effective_plans
from power_grid_env.closed_loop_protection import verify_closed_loop
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.closed_loop_protection_reference import run as compare


def revision(original,candidate):
    a=ProtectionEnvelope.model_validate(original).model_dump(mode='json')
    b=ProtectionEnvelope.model_validate(candidate).model_dump(mode='json')
    if a['base']['primary']['curve']!='extremely_inverse' or a['base']['backup']['curve']!='extremely_inverse':raise ValueError('EI revision protocol requires EI stages')
    before=a['base']['backup']['time_multiplier'];after=b['base']['backup']['time_multiplier']
    critical_a=copy.deepcopy(a);critical_b=copy.deepcopy(b)
    for x in [critical_a,critical_b]:
        x['id']='<label>';x['base']['backup']['time_multiplier']='<selected>'
    if critical_a!=critical_b:raise ValueError('requirements or other settings changed beyond backup TMS and label')
    if not after<before:raise ValueError('explicit faster backup TMS required')
    return dict(original_sha256=digest(encoded(a)),candidate_sha256=digest(encoded(b)),
        unchanged_requirements_sha256=digest(encoded(critical_a)),
        original_backup_TMS=before,candidate_backup_TMS=after,
        allowed_changed_fields=['id','base.backup.time_multiplier'],requirements_unchanged=True)


def negative_control(root,owner):
    expected=next(p for n,p,_ in effective_plans(owner) if n=='corner-0011')
    checked=verify_closed_loop(root,expected_plan=expected)
    if not checked['completed'] or not checked['numerically_qualified'] or checked['passes_declared_profile']:
        raise ValueError('complete numerically qualified rejected original control required')
    rows={r['id']:r for r in checked['actions']}
    if rows['fault-measurement_loss']['passed'] or any(not r['passed'] for id,r in rows.items() if id!='fault-measurement_loss'):
        raise ValueError('unexpected original failure mode')
    progress=[]
    for level in range(3):
        report=read_json(Path(root)/'studies/fault-measurement_loss'/f'grid-{level}'/'result.json')
        if any(e['kind']=='network_relay_trip' and e.get('equipment_id')=='backup-oc' for e in report['event_log']):raise ValueError('original backup unexpectedly tripped')
        peak=max(report['channels']['backup-oc.progress'])
        if peak>=1:raise ValueError('original backup progress must remain below trip threshold')
        progress.append(peak)
    return dict(verified=True,accepted=False,numerically_qualified=True,manifest_sha256=checked['manifest_sha256'],
        failed_case='fault-measurement_loss',maximum_backup_progress_by_grid=progress,
        reason='backup fails to trip before prescribed fault removal; requirements retained')


def run(original_radau,original_dae,candidate_radau,candidate_dae,control_radau,control_dae,out):
    changes=[revision(original_radau,candidate_radau),revision(original_dae,candidate_dae)]
    if any(c['original_backup_TMS']!=.08 or c['candidate_backup_TMS']!=.06 for c in changes):raise ValueError('declared .08 to .06 protocol required')
    controls=[negative_control(control_radau,original_radau),negative_control(control_dae,original_dae)]
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    paired=compare(control_radau,control_dae,root/'original-paired-reference')
    if not paired['passed']:raise ValueError('original rejection not reproducible across solvers')
    result=dict(passed=True,code_sha256=package_fingerprint(),revisions=changes,original_controls=controls,
        original_accepted=False,original_physical_grid_runs=36,original_reference_pairs=18,
        candidate_requires_separate_full_corner_verification=True,
        independent_differential_trajectory=False,normative_approval=False,
        producer_sha256=digest(Path(__file__).read_bytes()))
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['original-radau','original-dae','candidate-radau','candidate-dae','control-radau','control-dae','out']:p.add_argument('--'+name,required=True)
    a=p.parse_args();r=run(read_json(a.original_radau),read_json(a.original_dae),read_json(a.candidate_radau),read_json(a.candidate_dae),a.control_radau,a.control_dae,a.out)
    print(encoded(dict(passed=r['passed'],original_accepted=False)).decode())
