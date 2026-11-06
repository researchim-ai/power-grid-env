import pytest
from power_grid_env.motor_catalog import CATALOG_IDS,catalog_entry,catalog_study
from power_grid_env.flux_machine import FluxMachinePassport


@pytest.mark.parametrize('identifier',CATALOG_IDS)
def test_catalog_roundtrip_and_independent_mutability(identifier):
    a=catalog_entry(identifier);b=catalog_entry(identifier)
    assert FluxMachinePassport.model_validate(a['motor'].model_dump(mode='json'))==a['motor']
    a['motor'].cages[0].resistance_ohm=999
    assert b['motor'].cages[0].resistance_ohm!=999
    network,settings=catalog_study(identifier)
    assert settings.motors[0].node_id in {n.id for n in network.connectivity_nodes}
    assert 'Authored' in b['motor'].provenance['mechanical']
    if b['motor'].thermal:assert 'not measured' in b['motor'].provenance['thermal']


def test_exact_inverse_gamma_mapping():
    p=catalog_entry('aalto-linear-fan')['motor'];g=.224/(.224+.021)
    assert p.unsaturated_h==pytest.approx(.245)
    assert p.cages[0].resistance_ohm*g*g==pytest.approx(2.1)
    assert p.cages[0].leakage_h*g==pytest.approx(.021)


def test_unknown_rejected():
    with pytest.raises(ValueError):catalog_entry('invented-oem')
