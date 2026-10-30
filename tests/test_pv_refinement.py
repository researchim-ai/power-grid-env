"""PV refinements preserve original setpoints and compare to independent RMS PF."""
from pathlib import Path
import numpy as np
import pytest
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.backend_opendss import solve_network
from power_grid_env.results import SolveOptions


def test_two_pv_mesh_converges_at_original_load_and_matches_andes():
    s=StudySpec.model_validate_json(Path('cases/studies/rms_two_machine.json').read_text())
    result=solve_network(s.network)
    assert result.status=='converged',result.diagnostics
    assert any('pv_refinement' in a for a in result.diagnostics['attempts'])
    assert result.diagnostics['pq_setpoint_error_pu']<1e-5
    assert result.diagnostics['kcl_residual_pu']<1e-8
    rms=run_study(s);assert rms['completed']
    for v in result.voltages:
        if v.phase=='A':
            assert abs(v.complex)/v.v_base_ln_v==pytest.approx(rms['initial']['bus_voltage_pu'][v.bus_id],abs=1e-5)
            assert np.angle(v.complex)==pytest.approx(rms['initial']['bus_angle_rad'][v.bus_id],abs=1e-4)
    assert all('vpu=' in command and 'vminpu=0.9' in command for command in result.script.splitlines() if command.startswith('New Generator.') and 'model=3' in command)
    with pytest.raises(ValueError):SolveOptions(max_pv_refinements=-1)


def test_disabled_refinement_is_visible_and_continuation_restores_full_load():
    s=StudySpec.model_validate_json(Path('cases/studies/rms_two_machine.json').read_text())
    r=solve_network(s.network,SolveOptions(max_pv_refinements=0,max_iterations=1))
    assert r.status=='numerically_not_converged'
    assert r.diagnostics['attempts'][-1]['restored_requested_load'] is True


def test_dss_import_distinguishes_vpu_from_model_band_and_preserves_legacy():
    from power_grid_env.dss_devices import _generator
    def imported(extra):
        context={'terminals':[],'report':{'mappings':[]},'ensure_node':lambda bus,kv:bus}
        args={'bus1':'bus','kv':'10','phases':'3','model':'3','kw':'100','minkvar':'-40','maxkvar':'50',**extra}
        return _generator('pv',args,context)
    g=imported({'vpu':'1.03','vminpu':'0.9','vmaxpu':'1.1'})
    assert g.v_set_pu==1.03 and g.v_min_pu==.9 and g.v_max_pu==1.1
    old=imported({'vminpu':'1.02','vmaxpu':'1.02'})
    assert old.v_set_pu==old.v_min_pu==old.v_max_pu==1.02
    with pytest.raises(ValueError):imported({'vminpu':'.9','vmaxpu':'1.1'})
