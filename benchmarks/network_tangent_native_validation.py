"""Same physical NA-10 inputs with explicit direct network analytic tangents."""
from pathlib import Path
import argparse,json
from benchmarks.eight_machine_validation import qualify as eight_qualify,case as original,NAMES
from power_grid_env.model import canonical_hash
from power_grid_env.study_spec import StudySpec


def case(name):
    baseline=original(name);s=baseline.model_copy(deep=True);s.coupled.jacobian_network='direct_solve'
    s=StudySpec.model_validate(s.model_dump(mode='json'))
    check=s.model_copy(deep=True);check.coupled.jacobian_network='cached_ports'
    if canonical_hash(check)!=canonical_hash(baseline):raise ValueError('physical inputs changed beyond network tangent policy')
    return s


def qualify(out,root):
    expected={name:canonical_hash(case(name)) for name in NAMES}
    result=eight_qualify(out,root,case_factory=case,expected_hashes=expected)
    mapping={name:dict(baseline_sha256=canonical_hash(original(name)),direct_sha256=expected[name],only_changed_setting='jacobian_network') for name in NAMES}
    Path(out,'physical-case-mapping.json').write_text(json.dumps(mapping,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args();qualify(a.out,a.root)
