"""КЗ с ЭДС двигателя, отключение по этому току и срыв момента на том же напряжении."""

from power_grid_env.fault_event import qualify


def test_motor_fed_fault_links_the_network_the_relay_and_the_stall():
    report = qualify()
    assert report["verdict"] == "pass", report
    assert report["voltage_error_pu"] < 1e-5
    assert report["fault_current_error"] < 1e-5
    assert report["motor_current_error"] < 1e-5
    assert report["iec_gap"] > 0.02
    assert report["fault_current_a"] > 1000.0
    assert report["field_status"] == "waiting_for_data"
    assert report["not_a_field_measurement"] is True
