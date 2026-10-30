"""Площадка с двумя вводами не получает ток по формуле одного источника."""

from power_grid_env.plant_sc import normative_three_phase, qualify_plant_faults


def test_two_infeeds_keep_their_own_fault_current_and_selectivity():
    report = qualify_plant_faults()
    assert report["verdict"] == "pass", report
    assert report["near_time_s"] < report["upstream_time_s"]
    assert report["breaking_factor"] <= 1.0
    assert report["standard_compliance_claimed"] is False
    assert report["vendor_relay_claimed"] is False
    refused = normative_three_phase(2, 110_000.0, 1.1, 0.4 + 2.0j)
    assert refused["applicable"] is False
    assert refused["current_a"] is None
