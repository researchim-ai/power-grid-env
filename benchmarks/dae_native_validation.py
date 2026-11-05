"""Frozen eight-machine native corpus for the index-1 DAE and event policy."""
from pathlib import Path
import argparse,json
from benchmarks.eight_machine_validation import qualify as eight_qualify,case as original,NAMES
from power_grid_env.model import canonical_hash
from power_grid_env.study_spec import StudySpec


def case(name):
    s=original(name);s.coupled.jacobian_network='direct_solve';s.coupled.integration_method='network_dae';s.coupled.event_policy='coincident_roots_v1'
    return StudySpec.model_validate(s.model_dump(mode='json'))


def qualify(out,root):
    expected={n:canonical_hash(case(n)) for n in NAMES}
    result=eight_qualify(out,root,case_factory=case,expected_hashes=expected)
    Path(out,'physical-case-mapping.json').write_text(json.dumps({n:dict(baseline_sha256=canonical_hash(original(n)),dae_sha256=expected[n],changed_settings=['jacobian_network','integration_method','event_policy']) for n in NAMES},indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args();qualify(a.out,a.root)
