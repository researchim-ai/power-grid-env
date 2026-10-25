"""Сценарный вызов инструментов без внешнего LLM."""

from pathlib import Path

from power_grid_env.importer import import_network
from power_grid_env.llm_tools import ToolSession
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine, agent_model

network, _report = import_network(
    Path("cases/acceptance/feeder_north/feeder.dss"),
    Path("cases/acceptance/feeder_north/pge-import.yaml"),
)
scenario = Scenario(id="llm-demo", horizon_s=60, control_interval_s=60, settle_s=600, fault_line_id="damaged", fault_kind="3ph", fault_phases=["A", "B", "C"], fault_r_ohm=1.0)
engine = Engine(network, scenario)
engine.reset()
tools = ToolSession(engine, agent_model(network, scenario))
print(tools.call("read_procedure"))
plan = tools.call("simulate_plan", {"commands": [{"kind": "switch", "equipment_id": "sw_up", "command": "open"}]})
print(plan["status"], plan["remaining_quota"])
print(tools.call("submit_plan", {"action": {"kind": "hold"}}))
