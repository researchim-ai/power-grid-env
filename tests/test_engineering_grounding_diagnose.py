from benchmarks.engineering_grounding_diagnose import diagnose


def test_correct_digit_in_code_fence_remains_operational_failure():
    r=diagnose('```json\n{"value":"216.12"}\n```',dict(value='216.12'))
    assert not r['operational_success'] and r['single_fence']
    assert r['diagnostic_unwrapped_content']['semantic'] and not r['strict']['format_valid']


def test_wrong_value_or_unstructured_prose_is_not_rescued():
    assert not diagnose('```json\n{"value":"100"}\n```',dict(value='216.12'))['diagnostic_unwrapped_content']['semantic']
    assert not diagnose('Answer is {"value":"216.12"}',dict(value='216.12'))['operational_success']


def test_raw_valid_json_has_no_fence_regression():
    r=diagnose('{"value":"216.12"}',dict(value='216.12'))
    assert r['operational_success'] and not r['single_fence']
