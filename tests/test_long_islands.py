import pytest
import numpy as np
from benchmarks.long_islands import case,reference,compare
from power_grid_env.study_spec import run_study,StudySpec


@pytest.mark.parametrize('variant',['matched','mismatch'])
def test_independent_islands_have_nontrivial_frequency_and_safe_permissions(variant):
    s=case(variant=variant);assert StudySpec.model_validate_json(s.model_dump_json())==s
    r=reference(variant)
    assert r['maximum_simultaneous_islands']==3
    assert max(abs(np.array(r['channels']['g_a.omega'])-1))>5e-5
    blocked=[e for e in r['event_log'] if e['outcome']=='blocked']
    assert bool(blocked)==(variant=='mismatch')
    assert r['time_s'][-1]==45.


def test_bad_island_variant_rejected():
    with pytest.raises(ValueError):case(variant='unknown')


def test_oel_located_branch_is_not_undone_by_same_clock_same_state_refresh():
    import numpy as np
    from power_grid_env.generator_oel2c import OEL2CDynamics,OEL2CPassport
    p=OEL2CPassport(generator_id='g',rated_field_reaction_pu=1.);kernel=OEL2CDynamics(p);x=kernel.initial(p.ITfPu/p.KScale)
    eps=64*np.finfo(float).eps;x[0]=p.ITfPu-2*eps
    kernel.context['scaled_low']=True;log=[]
    kernel.apply({'branch_scaled_low'},18.,x,log)
    assert not kernel.context['scaled_low']
    kernel.apply(set(),18.,x,log)
    assert not kernel.context['scaled_low']
    assert len([e for e in log if e['transition']=='scaled_low'])==1
    # A genuine discrete state change at the same clock remains observable.
    x[0]=p.ITfPu-.01;kernel.apply(set(),18.,x,log)
    assert kernel.context['scaled_low']
