from benchmarks.noisy_identification_acceptance import equivalent_restored_result


def test_passed_attempt_must_be_byte_equivalent_including_timing():
    original = dict(passed=True, passport={'R': 3.7}, elapsed_s=2.)
    assert equivalent_restored_result(original, dict(original), True)
    assert not equivalent_restored_result(original, dict(original, elapsed_s=3.), True)


def test_rejected_rerun_can_change_only_elapsed_wall_time():
    original = dict(passed=False, passport={'R': 3.7}, elapsed_s=2., rhs_evaluations=100, reason='raw_residual_quality_rejected')
    assert equivalent_restored_result(original, dict(original, elapsed_s=3.), False)
    assert not equivalent_restored_result(original, dict(original, passport={'R': 3.8}), False)
    assert not equivalent_restored_result(original, dict(original, rhs_evaluations=101), False)
    assert not equivalent_restored_result(original, dict(original, reason='timeout'), False)


def test_success_status_cannot_be_changed_or_omitted():
    assert not equivalent_restored_result(dict(passed=False), dict(passed=True), False)
    assert not equivalent_restored_result({}, {}, False)
