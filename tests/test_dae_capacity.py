"""Explicit expanded capacity, bounded exports and frozen local Schur blocks."""
import numpy as np
import pytest
from pydantic import ValidationError
from tests.test_sparse_jacobian import profile
from tests.test_network_tangents import PROFILES
from power_grid_env.dae_linearization import DAEBlocks,device_groups


@pytest.mark.parametrize('name',PROFILES)
def test_exact_cached_local_schur_action_for_real_and_complex_directions(name):
    m,x=profile(name);cb=m.integration_jacobian();blocks=DAEBlocks(m,cb,0.,x,32,lambda:None,lambda:None);rng=np.random.default_rng(16);v=rng.normal(size=len(x))+1j*rng.normal(size=len(x))
    assert np.allclose(blocks.action(v),cb(0.,x)@v,rtol=1e-10,atol=2e-9)
    assert blocks.stored_local_scalar_slots<128*len(x)
    assert sorted(j for group,_ in device_groups(m,len(x)) for j in group)==list(range(len(x)))


@pytest.mark.parametrize('counts',[(512,64,16),(2000,128,32)])
def test_extended_capacity_requires_explicit_profile_and_solver_policy(counts):
    from benchmarks.dae_cases import scale_case
    from power_grid_env.study_spec import StudySpec
    from power_grid_env.coupled_rms import validate_coupled
    s=scale_case(*counts);assert validate_coupled(s.network,s.coupled)['ok']
    for setting,value in [('capacity_profile','standard'),('jacobian_network','cached_ports'),('event_policy','legacy')]:
        changed=s.model_copy(deep=True);setattr(changed.coupled,setting,value)
        with pytest.raises(ValidationError):StudySpec.model_validate(changed.model_dump(mode='json'))


def test_monitored_line_export_is_explicit_and_does_not_change_physics():
    from benchmarks.jacobian_scaling_validation import case
    from power_grid_env.study_spec import run_study
    s=case('scale_classical_512');s.coupled.duration_s=.02;s.coupled.step_s=.005;s.coupled.events=[]
    a=run_study(s);s.coupled.monitored_lines=[];b=run_study(s);assert a['completed'] and b['completed']
    for k in b['channels']:assert np.array_equal(a['channels'][k],b['channels'][k]),k
    assert len(b['channels'])<len(a['channels']) and b['maximum_power_balance_residual_pu']<1e-8


def test_operated_lines_cannot_disappear_from_selected_export():
    from tests.test_coupled_lines import example
    from power_grid_env.coupled_rms import validate_coupled
    s=example('open_reclose');s.coupled.monitored_lines=[]
    assert not validate_coupled(s.network,s.coupled)['ok']
