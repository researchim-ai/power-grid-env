"""The formerly sensitive case must also survive no-op and passport changes."""
from pathlib import Path
from functools import lru_cache
import os
import pytest
from benchmarks.generator_esst1a_refinement import case,reset_distance
from benchmarks.generator_esst1a_joint_validation import compare
from benchmarks.mnlex2_guard_audit import audit
from power_grid_env.coupled_rms import CoupledEvent
from power_grid_env.study_spec import StudySpec,run_study


@pytest.fixture(scope='module')
def weak(tmp_path_factory):
    root=os.environ.get('PGE_OPENMODELICA_ROOT')
    if not root:pytest.skip('optional pinned OpenModelica runtime')
    from benchmarks.esst1a_network import run
    out=tmp_path_factory.mktemp('weak-root-invariance');base=case('two_limiters_sensitive');primary=run_study(base);assert primary['completed']
    @lru_cache(None)
    def result(tolerance,variant):
        s=base.model_copy(deep=True)
        if variant=='noop':s.coupled.events += [CoupledEvent(time_s=.1,kind='voltage_reference_step',equipment_id=g,value=0.) for g in ('g','g2')]
        if variant=='permutation':s.coupled.esst1a_exciters.reverse();s.coupled.underexcitation_limiters.reverse()
        s=StudySpec.model_validate(s.model_dump(mode='json'))
        b=run(s,out/(str(tolerance)+'-'+variant),Path(root),tolerance,native_uels=True)
        assert b['native_UEL_root_policy']=='continuous_atoms'
        assert audit(s,b)['passed']
        return s,b
    return base,primary,result


@pytest.mark.parametrize('tolerance',[1e-11,1e-12])
@pytest.mark.parametrize('variant',['noop','permutation'])
def test_weak_case_reset_clocks_and_sides_survive_metamorphic_changes(weak,tolerance,variant):
    base,primary,result=weak;_,a=result(tolerance,'baseline');_,b=result(tolerance,variant)
    self_check=reset_distance(a,b,True,True);assert self_check['passed'],self_check
    independent=reset_distance(primary,b,False,True);assert independent['passed'],independent


@pytest.mark.parametrize('tolerance',[1e-11,1e-12])
def test_weak_baseline_all_states_and_node_voltages_agree(weak,tolerance):
    base,primary,result=weak;_,b=result(tolerance,'baseline');check=compare(base,primary,b)
    assert check['passed'],check
