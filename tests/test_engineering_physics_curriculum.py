from collections import Counter
from benchmarks.engineering_physics_campaign import order


def test_finite_phase_has_six_presentations_per_exemplar_and_no_outcome_adaptation():
    indices=list(range(13));a=order(indices)
    assert len(a)==78 and a==order(indices) and Counter(a)=={i:6 for i in indices}
    assert all(set(a[i:i+13])==set(indices) for i in range(0,78,13))
