"""Read-only behavioral audit. Run: python -m reports.development_review.reproduce."""
import json
from pathlib import Path

from power_grid_env.analysis import ac_opf_two_bus
from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.importer import import_network
from power_grid_env.runner import _verdict
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine, agent_model


def base():
    b = NetBuilder("audit", "audit", 12.47)
    b.node("src")
    b.node("end")
    b.source("src", 0.02, 0.08)
    b.line_z1("src", "end", 0.1 + 0.2j, line_id="line")
    b.load("end", 100_000, 10_000, load_id="load")
    b.meter("end")
    return b


def run():
    results = {}
    b = base()
    b.inverter("end", s_rated_va=80_000, p_w=10_000, inverter_id="pv")
    net = b.build()
    engine = Engine(net, Scenario(id="setpoint", horizon_s=120, settle_s=600))
    engine.reset()
    before = engine.last_result
    record = engine.step_control({"kind": "setpoint", "equipment_id": "pv", "value": 50_000, "unit": "W"})
    fresh = solve_network(engine.net)
    injection = lambda result: -sum(f.p_into_w for f in result.flows if f.equipment_id == "pv")
    results["setpoint"] = {"accepted": record["acceptance"]["accepted"], "requested_w": 50_000,
        "recorded_w": injection(engine.last_result), "fresh_solve_w": injection(fresh),
        "result_object_unchanged": engine.last_result is before}
    engine = Engine(base().build(), Scenario(id="profile", horizon_s=120, settle_s=600,
        load_profile={"load": [[0, 1], [30, 2]]}))
    engine.reset()
    engine.step_control({"kind": "hold"})
    results["profile"] = {"time_s": engine.time_s, "load_w": sum(engine.net.loads[0].p_w.values()),
        "measurement_produced_times": [m["produced_s"] for m in engine.measurements]}
    try:
        other = Engine(base().build(), engine.scenario)
        other.restore(json.loads(json.dumps(engine.snapshot())))
        results["checkpoint_json"] = {"restored": True}
    except Exception as exc:
        results["checkpoint_json"] = {"restored": False, "error": str(exc)}
    fault = Scenario(id="hidden", fault_line_id="line", fault_disclosed=False, fault_r_ohm=1.234567)
    engine = Engine(base().build(), fault)
    engine.reset()
    intercepted = []
    def capture(network):
        intercepted.extend(f.model_dump() for f in network.study_faults)
        return solve_network(network)
    engine.solve = capture
    reply = engine.what_if([], agent_model(engine.base_network, fault))
    results["what_if"] = {"fault_disclosed": False, "truth_fault_sent_to_what_if": intercepted,
        "accepted": reply["accepted"]}
    b = base()
    b.generator("end", 10_000, generator_id="gen")
    engine = Engine(b.build(), Scenario(id="generator", horizon_s=120, settle_s=600))
    engine.reset()
    record = engine.step_control({"kind": "setpoint", "equipment_id": "gen", "value": 50_000, "unit": "W"})
    results["generator_setpoint"] = {"accepted": record["acceptance"]["accepted"], "stored_w": engine.net.generators[0].p_w}
    results["ac_opf"] = ac_opf_two_bus(0.5, 0.1, 0.25, cost=20, pmax=1)
    results["empty_verdict"] = _verdict([], [])
    results["scenario_zero_interval_accepted"] = Scenario(id="zero", control_interval_s=0).control_interval_s == 0
    original = Path("cases/acceptance/feeder_north/feeder.dss").read_text()
    modified = Path("/tmp/pge-audit-unsupported.dss")
    modified.write_text(original + "\nNew Transformer.extra phases=3 windings=2\nRedirect hidden.dss\n")
    imported, report = import_network(modified, Path("cases/acceptance/feeder_north/pge-import.yaml"))
    results["unsupported_import"] = {"accepted": imported is not None, "errors": report["errors"],
        "warnings": report["warnings"], "transformers": len(imported.transformers) if imported else None}
    b = NetBuilder("relay", "relay", 12.47)
    b.node("src"); b.node("a"); b.node("end")
    b.source("src", 0.02, 0.08)
    b.switch("src", "a", switch_id="sw")
    b.line_z1("a", "end", 0.1 + 0.2j, line_id="line")
    b.load("end", 100_000, 10_000, load_id="load")
    engine = Engine(b.build(), Scenario(id="relay", horizon_s=120, settle_s=600,
        protection=[{"id":"r", "equipment_id":"sw", "kind":"overcurrent", "pickup":0.1,
            "curve":"definite", "time_multiplier_s":1}]))
    engine.reset()
    record = engine.step_control({"kind":"hold"})
    results["relay_switch"] = {"switch_closed": engine.net.switches[0].closed,
        "relay_signal": engine._relay_signal(engine.relays[0]),
        "switch_flow_records": sum(f.equipment_id == "sw" for f in engine.last_result.flows),
        "operations_recorded":record["operations"]}
    out = Path("reports/development_review/probes.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(out.read_text())


if __name__ == "__main__":
    run()
