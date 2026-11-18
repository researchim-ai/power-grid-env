import copy
import json
import pytest

from benchmarks.qwen35_public_numeric_baseline import public_numeric_policy
from power_grid_env.design_frame import indication_rule


def messages():
    motors = [dict(motor_id=k, node_id='n', connected=connected, speed_indication_pu=speed,
                   voltage_indication_pu=1., current_indication_pu=1.)
              for k, connected, speed in (('m', True, .9), ('m2', True, .899), ('m3', False, 1.))]
    return [dict(role='user', content=[dict(type='text', text='Instrument observation: '+
        json.dumps(dict(motors=motors, rule=indication_rule())))])]


def test_policy_uses_displayed_threshold_and_disconnection_priority():
    answer = public_numeric_policy(messages())
    assert answer == dict(motors=dict(m='at_running_speed', m2='below_running_speed', m3='disconnected'),
                          insufficient_observation=False)


def test_extra_private_metadata_is_not_a_policy_input_and_input_is_unchanged():
    original = messages()
    dirty = copy.deepcopy(original)
    dirty[0]['expected'] = dict(motors={})
    before = copy.deepcopy(dirty)
    assert public_numeric_policy(original) == public_numeric_policy(dirty)
    assert dirty == before


def test_images_are_abstained_instead_of_reading_hidden_state():
    prompt = [dict(role='user', content=[dict(type='image', image='images/opaque.png')])]
    assert public_numeric_policy(prompt) == dict(motors={}, insufficient_observation=True)


def test_ambiguous_or_unrecognized_observations_fail_closed():
    duplicate = messages()
    duplicate[0]['content'] *= 2
    with pytest.raises(ValueError): public_numeric_policy(duplicate)
    unsupported = messages()
    unsupported[0]['content'][0]['text'] = unsupported[0]['content'][0]['text'].replace('0.9', '0.8')
    with pytest.raises(ValueError): public_numeric_policy(unsupported)
