import copy
import pytest
from benchmarks.qwen35_mixed_constant_control import constant_policy
from power_grid_env.design_response_schema import schema_text


def messages():
    return [dict(role='user', content=[dict(type='image', image='/must/not/be/opened.png'),
        dict(type='text', text='Instrument observation: private-looking values are ignored'),
        dict(type='text', text=schema_text(['m', 'm2', 'm3']))])]


def test_control_uses_only_public_static_ids_and_ignores_pixels_and_measurements():
    original = messages()
    before = copy.deepcopy(original)
    answer = constant_policy(original, 'at_running_speed')
    assert answer == dict(insufficient_observation=False, motors={m:'at_running_speed' for m in ('m','m2','m3')})
    assert original == before
    original[0]['content'][0]['image'] = '/different/nonexistent.png'
    original[0]['content'][1]['text'] = 'Instrument observation: all states changed'
    assert constant_policy(original, 'at_running_speed') == answer


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'tampered'])
def test_control_rejects_missing_ambiguous_or_changed_contract(fault):
    rows = messages()
    if fault == 'missing': rows[0]['content'].pop()
    elif fault == 'duplicate': rows[0]['content'].append(rows[0]['content'][-1])
    else: rows[0]['content'][-1]['text'] = rows[0]['content'][-1]['text'].replace('at_running_speed', 'guessed')
    with pytest.raises(ValueError): constant_policy(rows, 'at_running_speed')


def test_control_cannot_choose_an_undeclared_class():
    with pytest.raises(ValueError): constant_policy(messages(), 'all_good')
