"""Каталог корпуса не переносит допуск и успех одного семейства на другое."""

from benchmarks.external.corpus import CATALOG, HELD_OUT_FAMILY, analytic_member, assess, tiers, two_feeder_qsts
from benchmarks.external.simbench_network import CROSS_SOLVER_VOLTAGE_PU
from power_grid_env.phase_oracle import ANGLE_RAD, KCL_PU, VOLTAGE_PU


def test_catalog_keeps_families_and_the_strict_analytic_tier():
    families = {item["family"] for item in CATALOG}
    assert len(CATALOG) >= 10
    assert len(families) >= 4
    assert HELD_OUT_FAMILY in families
    assert tiers()["analytic"]["voltage_pu"] == VOLTAGE_PU == 1e-5
    assert tiers()["analytic"]["angle_rad"] == ANGLE_RAD == 1e-4
    assert tiers()["analytic"]["kcl_pu"] == KCL_PU == 1e-8
    assert tiers()["cross_solver_voltage"]["voltage_pu"] == CROSS_SOLVER_VOLTAGE_PU == 1e-4
    assert tiers()["same_engine"]["voltage_pu"] == 1e-5
    member = analytic_member()
    assert member["verdict"] == "pass", member
    assert member["voltage_error_pu"] < 1e-5
    assert member["angle_error_rad"] < 1e-4
    assert member["kcl_pu"] < 1e-8


def test_missing_channel_disappeared_node_and_borrowed_assumptions_fail():
    ieee = [_same_engine(item["id"]) for item in CATALOG if item["family"] == "ieee-pes"]
    report = assess(ieee)
    assert report["qualified_families_verdict"] == "incomplete"
    assert report["held_out_verdict"] == "incomplete"
    assert report["verdict"] == "incomplete"
    assert report["field_status"] == "waiting_for_data"
    dropped = _same_engine("ieee13")
    dropped["nodes"] = ["1", "2", "3"]
    assert assess([dropped])["rows"][0]["reason"] == "missing_node"
    silent = _same_engine("ieee13")
    silent["channels"] = ["voltage", "losses", "power", "current"]
    assert assess([silent])["rows"][0]["reason"] == "missing_channel"
    borrowed = _same_engine("ieee34")
    borrowed["inherited_simplifications"] = ["Z0 в CSV нет"]
    assert assess([borrowed])["rows"][1]["reason"] == "inherited_simbench"


def test_simbench_tier_is_not_renamed_and_two_feeders_without_csv_stay_blocked(tmp_path):
    renamed = {
        "id": "1-LV-rural1--0-sw",
        "angles_compared": True,
        "same_engine_gate_claimed": True,
        "assumptions": ["явные"],
        "voltage_error_pu": 1e-6,
    }
    assert assess([renamed])["rows"][6]["reason"] == "tier_renamed"
    bare = {"id": "1-MV-rural--0-sw", "angles_compared": False, "same_engine_gate_claimed": False, "voltage_error_pu": 1e-5}
    assert assess([bare])["rows"][7]["reason"] == "assumptions"
    blocked = two_feeder_qsts(None, tmp_path / "missing.csv")
    assert blocked["status"] == "blocked"
    assert blocked["synthetic_profile_used"] is False
    assert blocked["intervals"] == 0
    (tmp_path / "a.csv").write_text("t,p\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("t,p\n", encoding="utf-8")
    ready = two_feeder_qsts(tmp_path / "a.csv", tmp_path / "b.csv")
    assert ready["status"] == "ready"
    assert ready["synthetic_profile_used"] is False
    assert ready["intervals"] == 0


def _same_engine(case_id: str) -> dict:
    return {
        "id": case_id,
        "nodes": ["1", "2", "3", "4"],
        "channels": ["voltage", "angle", "losses", "power", "current"],
        "voltage_error_pu": 1e-8,
        "angle_error_rad": 1e-7,
    }
