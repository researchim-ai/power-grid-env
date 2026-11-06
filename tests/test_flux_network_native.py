"""Original native AC ports/flux RHS versus sparse current-coordinate network."""
import copy,os
from pathlib import Path
import numpy as np
import pytest
from benchmarks.flux_network_native import NAMES,example,reference,compare,NativeCircuit
from benchmarks.extended_equipment_validation import verify_native
from power_grid_env.flux_network import run_flux_network

@pytest.fixture(scope='module')
def native_wheel():
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('explicit hash-pinned motulator wheel required')
    verify_native(Path(wheel));return Path(wheel)

@pytest.mark.parametrize('name',NAMES)
@pytest.mark.filterwarnings('error::RuntimeWarning')
def test_complete_nonlinear_network_matches_original_native_flux_rhs(name,native_wheel):
    net,settings=example(name);native=reference(net,settings);primary=run_flux_network(net,settings)
    check=compare(primary,native);assert check['passed'],check
    assert primary['maximum_energy_relative_residual']<2e-6


def test_native_solver_does_not_call_primary_kernel_or_topology(native_wheel,monkeypatch):
    net,settings=example('meshed_pair');settings.duration_s=.002;settings.step_s=.001
    def forbidden(*a,**kw):pytest.fail('native reference called production equation or stamp')
    import power_grid_env.flux_network as production
    import power_grid_env.flux_machine as machines
    import power_grid_env.topology as topology
    monkeypatch.setattr(production,'FluxNetwork',forbidden);monkeypatch.setattr(production,'run_flux_network',forbidden)
    monkeypatch.setattr(machines.GammaWinding,'quantities',forbidden);monkeypatch.setattr(machines.GammaWinding,'derivatives',forbidden)
    monkeypatch.setattr(topology,'compile_topology',forbidden);monkeypatch.setattr(production,'transformer_parameters',forbidden)
    native=reference(net,settings);assert native['completed'] and native['maximum_kcl_relative']<1e-6


def test_native_step_refinement_is_below_declared_trace_uncertainty(native_wheel):
    net,settings=example('meshed_pair');settings.duration_s=.008
    coarse=reference(net,settings);fine=reference(net,settings,rtol=1e-12,max_step=.00005)
    for key,a in coarse['channels'].items():assert np.max(abs(np.array(a)-fine['channels'][key]))<1e-6,key

@pytest.fixture(scope='module')
def traces(native_wheel):
    net,settings=example('meshed_thermal_sag');return run_flux_network(net,settings),reference(net,settings)

@pytest.mark.parametrize('defect',['missing_primary','missing_native','nan','nan_native','nan_kcl','large_kcl','bad_axis','empty','wrong_current','wrong_voltage','wrong_topology'])
def test_network_comparison_fails_closed(traces,defect):
    primary,native=copy.deepcopy(traces);key='m1.current_alpha_a'
    if defect=='missing_primary':del primary['channels'][key]
    elif defect=='missing_native':del native['channels'][key]
    elif defect=='nan':primary['channels'][key][1]=float('nan')
    elif defect=='nan_native':native['channels'][key][1]=float('nan')
    elif defect=='nan_kcl':native['maximum_kcl_relative']=float('nan')
    elif defect=='large_kcl':native['maximum_kcl_relative']=.001
    elif defect=='bad_axis':native['time_s'][1]=0.
    elif defect=='empty':native['channels']={}
    else:
        altered=key if defect=='wrong_current' else next(k for k in primary['channels'] if '.voltage_alpha_v' in k) if defect=='wrong_voltage' else 'tie.current_alpha_a'
        primary['channels'][altered][1]+=1.
    if defect.startswith('wrong_'):assert not compare(primary,native)['passed']
    else:
        with pytest.raises((ValueError,KeyError)):compare(primary,native)


def test_multi_cage_is_not_mislabeled_as_native_original(native_wheel):
    net,settings=example('meshed_two_cages');settings.duration_s=.002;settings.step_s=.001
    actual=reference(net,settings)
    assert not actual['original_motulator_rhs_and_ac_port']
    assert set(actual['component_origins'].values())=={'independently_authored_multicage_flux_equations'}
