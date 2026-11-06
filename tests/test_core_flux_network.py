import pytest
from benchmarks.flux_network_native import example
from power_grid_env.flux_network import run_flux_network,FluxNetwork
from power_grid_env.flux_machine import FluxMachinePassport

@pytest.mark.parametrize('thermal',[False,True])
def test_core_loss_is_positive_and_accounted_in_total_energy(thermal):
    n,s=example('meshed_core_thermal' if thermal else 'core_radial');s.duration_s=.012;s.events=[]
    r=run_flux_network(n,s);assert r['completed'],r
    assert r['maximum_energy_relative_residual']<2e-7 and max(r['channels']['m1.core_loss_w'])>0
    assert r['scope']['core_loss_model']=='constant_eddy_current_conductance'
    if thermal:assert r['channels']['m1.stator_temperature_c'][-1]>20.


def test_mixed_lossless_core_fleet_is_explicitly_rejected():
    n,s=example('core_radial');s.motors[0].core_loss_s=0.
    r=run_flux_network(n,s);assert not r['completed'] and 'mixed fleets' in r['detail']


def test_core_parameter_requires_provenance_and_explicit_flux_coordinate_profile():
    n,s=example('core_radial');p=s.motors[0].model_dump();del p['provenance']['core_loss']
    with pytest.raises(ValueError):FluxMachinePassport.model_validate(p)
    with pytest.raises(ValueError,match='flux-coordinate'):FluxNetwork(n,s)


def test_auxiliary_core_channels_cannot_overwrite_equipment():
    n,s=example('transformer_core_excitation');s.motors[0].id='tx.core'
    r=run_flux_network(n,s);assert not r['completed'] and 'collides' in r['detail']
