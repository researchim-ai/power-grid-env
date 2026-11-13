"""Join three declared full-family profiles without hiding EI design revision.

Current-code SI evidence may be reused by hash; copies/checks are not new runs.
"""
import argparse
import copy
from pathlib import Path
from typing import Literal
from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract
from power_grid_env.protection_envelope import ProtectionEnvelope,effective_plans
from power_grid_env.protection_envelope_store import verify_store
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.protection_family_revision import revision,negative_control
from benchmarks.full_protection_envelope_reference import stable_index

FAMILIES={'standard_inverse','very_inverse','extremely_inverse'}

class FamilySources(Contract):
    family: Literal['standard_inverse','very_inverse','extremely_inverse']
    radau_owner: str
    dae_owner: str
    radau_store: str
    dae_store: str
    paired_reference: str
    reference_producer: str
    duty: str
    reused_previous_current_code: bool=False

class Sources(Contract):
    schema_version: Literal['power-grid-env.three-family-acceptance/v1']='power-grid-env.three-family-acceptance/v1'
    families: list[FamilySources]=Field(min_length=3,max_length=3)
    original_ei_radau_owner: str
    original_ei_dae_owner: str
    original_ei_radau_study: str
    original_ei_dae_study: str
    revision_evidence: str
    @model_validator(mode='after')
    def coverage(self):
        if {f.family for f in self.families}!=FAMILIES:raise ValueError('exactly three distinct inverse families required')
        return self


def requirements(envelope):
    """Common equipment/fault/acceptance inputs, excluding declared choices."""
    e=ProtectionEnvelope.model_validate(envelope).model_dump(mode='json')
    p=e['base'];p.pop('id');p.pop('integration_method')
    for stage in ['primary','backup']:
        for key in ['curve','time_multiplier','source']:p[stage].pop(key)
    return p


def family_record(f):
    a=read_json(f.radau_owner);b=read_json(f.dae_owner)
    left=ProtectionEnvelope.model_validate(a).model_dump(mode='json')
    right=ProtectionEnvelope.model_validate(b).model_dump(mode='json')
    if left['base']['integration_method']!='radau' or right['base']['integration_method']!='network_dae':raise ValueError('solver owner mapping differs')
    check=copy.deepcopy(right);check['base']['integration_method']='radau'
    if check!=left:raise ValueError('paired owners differ beyond method')
    for stage in ['primary','backup']:
        if left['base'][stage]['curve']!=f.family:raise ValueError('family label differs from relay passports')
    r=verify_store(f.radau_store,expected_envelope=a);d=verify_store(f.dae_store,expected_envelope=b)
    if not r['passes_all_enumerated_corners'] or not d['passes_all_enumerated_corners']:raise ValueError('all finite corners must pass this qualified family')
    ref=read_json(Path(f.paired_reference)/'summary.json');ref_hash=verify(f.paired_reference)
    if not ref['passed'] or ref['code_sha256']!=package_fingerprint():raise ValueError('current qualified reference required')
    if ref['producer_sha256']!=digest(Path(f.reference_producer).read_bytes()):raise ValueError('reference producer differs from pinned source')
    if [ref['radau_index_sha256'],ref['dae_index_sha256']]!=[r['index_manifest_sha256'],d['index_manifest_sha256']]:raise ValueError('reference uses different stores')
    names=[n for n,_,_ in effective_plans(a)]
    if [row['id'] for row in ref['rows']]!=names:raise ValueError('reference corner coverage differs')
    for row in ref['rows']:
        child=Path(f.paired_reference)/row['id'];value=read_json(child/'summary.json')
        if not row['passed'] or row['reference_manifest_sha256']!=verify(child) or not value['passed'] or len(value['rows'])!=18:raise ValueError('reference child coverage differs')
        if value['radau_manifest_sha256']!=verify(Path(f.radau_store)/'studies'/row['id']) or value['dae_manifest_sha256']!=verify(Path(f.dae_store)/'studies'/row['id']):raise ValueError('reference child input identity differs')
    duty=read_json(Path(f.duty)/'summary.json');duty_hash=verify(f.duty)
    if not duty['passed'] or duty['code_sha256']!=package_fingerprint() or duty['fault_grid_checks']!=306:raise ValueError('qualified full duty reference required')
    if [v['index_sha256'] for v in duty['source_indices']]!=[r['index_manifest_sha256'],d['index_manifest_sha256']]:raise ValueError('duty uses different stores')
    if r['completed_grid_runs']!=306 or d['completed_grid_runs']!=306 or ref['grid_pairs']!=306 or ref['conditional_si_trace_checks']!=612:raise ValueError('full-profile run coverage differs')
    stable_index(f.radau_store,r['index_manifest_sha256']);stable_index(f.dae_store,d['index_manifest_sha256'])
    print(f.family,'full family evidence verified',flush=True)
    return dict(family=f.family,passed=True,backup_TMS=left['base']['backup']['time_multiplier'],
        requirements_sha256=digest(encoded(requirements(a))),physical_grid_runs=612,grid_pairs=306,fault_grid_checks=306,
        radau_index_sha256=r['index_manifest_sha256'],dae_index_sha256=d['index_manifest_sha256'],
        paired_reference_manifest_sha256=ref_hash,duty_manifest_sha256=duty_hash,
        reused_previous_current_code=f.reused_previous_current_code)


def run(sources,out):
    sources=Sources.model_validate(sources);rows=[family_record(f) for f in sources.families]
    if len({r['requirements_sha256'] for r in rows})!=1:raise ValueError('families use different engineering requirements')
    ei=next(f for f in sources.families if f.family=='extremely_inverse')
    changes=[revision(read_json(sources.original_ei_radau_owner),read_json(ei.radau_owner)),
             revision(read_json(sources.original_ei_dae_owner),read_json(ei.dae_owner))]
    if any(r['original_backup_TMS']!=.08 or r['candidate_backup_TMS']!=.06 for r in changes):raise ValueError('unexpected EI revision')
    controls=[negative_control(sources.original_ei_radau_study,read_json(sources.original_ei_radau_owner)),
              negative_control(sources.original_ei_dae_study,read_json(sources.original_ei_dae_owner))]
    # Numerical motor convergence alone must not disguise a failed clock audit
    # as an engineering counterexample. The original trace must have valid
    # inverse clocks and specifically missing backup action.
    for path in [sources.original_ei_radau_study,sources.original_ei_dae_study]:
        original=read_json(Path(path)/'result.json')
        if any(not audit['passed'] for action in original['actions'] for grid in action['grids'] for audit in grid['clock_audits'].values()):
            raise ValueError('original rejection has an unqualified relay clock')
    revision_root=Path(sources.revision_evidence);revision_hash=verify(revision_root)
    revision_result=read_json(revision_root/'summary.json')
    if revision_result['revisions']!=changes or revision_result['original_controls']!=controls or not revision_result['passed']:raise ValueError('EI revision evidence differs')
    result=dict(schema='power-grid-env.three-family-envelope-result/v1',passed=True,code_sha256=package_fingerprint(),families=rows,
        full_qualified_grid_runs=1836,reused_current_grid_runs=sum(r['physical_grid_runs'] for r in rows if r['reused_previous_current_code']),
        new_qualified_grid_runs=sum(r['physical_grid_runs'] for r in rows if not r['reused_previous_current_code']),
        rejected_original_control_grid_runs=36,solver_grid_pairs=918,conditional_SI_trace_checks=1836,measured_duty_checks=918,
        original_EI_accepted=False,original_inverse_clock_audits_passed=True,EI_backup_TMS_revision=[.08,.06],revision_manifest_sha256=revision_hash,
        continuous_uncertainty_qualified=False,independent_differential_trajectory=False,normative_approval=False,
        full_DES04_complete=False,reward=None,producer_sha256=digest(Path(__file__).read_bytes()))
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'sources.json').write_bytes(encoded(sources.model_dump(mode='json')))
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    r=run(read_json(a.input),a.out);print(encoded(dict(passed=r['passed'],qualified_runs=r['full_qualified_grid_runs'],original_EI_accepted=False)).decode())
