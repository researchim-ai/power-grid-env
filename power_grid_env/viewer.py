"""Инженерный просмотр: однолинейная схема, шкала времени, пределы и действия.

Привилегированные величины и наблюдение контроллера подписаны и не смешиваются.
Страница самодостаточна: обозначения вшиваются из assets/sld и не загружаются из сети.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from power_grid_env.model import Network
from power_grid_env.topology import compile_topology

_ASSET = Path(__file__).resolve().parent / "assets" / "sld"


def write_viewer(path: Path, bundle: dict[str, Any]) -> None:
    payload = json.dumps(bundle, ensure_ascii=False).replace("<", "\\u003c")
    symbols = "\n".join(asset.read_text(encoding="utf-8") for asset in sorted(_ASSET.glob("*.svg")))
    script = (_ASSET / "diagram.js").read_text(encoding="utf-8")
    page = _PAGE.replace("__SYMBOLS__", symbols).replace("__SCRIPT__", script).replace("__DATA__", payload)
    Path(path).write_text(page, encoding="utf-8")


_PAGE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Просмотр испытания</title>
<style>
body { margin: 0; font-family: "Segoe UI", "Liberation Sans", sans-serif; color: #1c1917; background: #f3f0e8; }
header { background: #1c2e28; color: white; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; }
.banner { font-weight: 600; }
main { display: grid; grid-template-columns: 320px 1fr; gap: 12px; padding: 12px; }
section { background: white; border: 1px solid #e4dfd4; border-radius: 8px; padding: 10px 12px; }
.scheme { grid-column: 1 / -1; }
.scheme-head { display: flex; justify-content: space-between; align-items: baseline; }
.scheme-head h3, section h3 { margin: 0 0 8px; }
#scheme-name { color: #57534e; font-size: 14px; }
#diagram { width: 100%; height: 680px; background: #fbfaf7; border-radius: 6px; }
#chart svg { width: 100%; height: 80px; background: transparent; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin-top: 8px; color: #44403c; font-size: 12px; }
.legend span { display: inline-flex; align-items: center; gap: 4px; }
.legend svg { width: 46px; height: 28px; background: transparent; }
.halo { opacity: 0; }
g.selected .halo { opacity: 1; }
g.alarm text { fill: #9f1239; font-weight: 700; }
.bad { color: #9f1239; font-weight: 700; }
button { margin: 2px 0; display: block; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
td, th { border-bottom: 1px solid #eee; padding: 4px; text-align: left; }
.swatch::before { content: ""; width: 18px; height: 4px; border-radius: 2px; display: inline-block; }
.swatch.hv::before { background: #9f1239; }
.swatch.mv::before { background: #b45309; }
.swatch.lv::before { background: #15803d; }
</style></head><body>
<header>
  <div class="banner" id="banner">привилегированный вид оценки</div>
  <div>
    <button id="priv" type="button">привилегированный вид</button>
    <button id="obs" type="button">только observation</button>
  </div>
</header>
<main>
  <section class="scheme">
    <div class="scheme-head"><h3>Однолинейная схема</h3><div id="scheme-name"></div></div>
    <svg id="diagram">
      <defs>
        __SYMBOLS__
        <pattern id="paper-grid" width="28" height="28" patternUnits="userSpaceOnUse">
          <path d="M28 0 H0 V28" fill="none" stroke="#ece7dc" stroke-width="0.7"/>
        </pattern>
      </defs>
      <rect id="paper" fill="url(#paper-grid)"/>
      <g id="scene"></g>
    </svg>
    <div class="legend" id="legend"></div>
  </section>
  <section><h3>Время и действия</h3><div id="timeline"></div><h3>Минимум напряжения фазы A</h3><div id="chart"></div></section>
  <section><h3>Величины и нарушения</h3><div id="detail"></div></section>
</main>
<script>
const data = __DATA__;
__SCRIPT__
</script></body></html>
"""


def _node_of(terminals: dict[str, dict[str, Any]], terminal_id: str) -> str:
    return terminals[terminal_id]["connectivity_node_id"]


def viewer_bundle(network_dump: dict[str, Any], steps: list[dict[str, Any]], v_min: float, v_max: float) -> dict[str, Any]:
    terminals = {item["id"]: item for item in network_dump["terminals"]}
    levels = {item["id"]: item["nominal_ll_v"] for item in network_dump["voltage_levels"]}
    nodes = [
        {"id": node["id"], "x": node.get("x"), "y": node.get("y"), "kv": levels[node["voltage_level_id"]] / 1000.0}
        for node in network_dump["connectivity_nodes"]
    ]
    lines = [
        {
            "id": line["id"],
            "from": _node_of(terminals, line["terminal_from"]),
            "to": _node_of(terminals, line["terminal_to"]),
        }
        for line in network_dump["lines"]
    ]
    switches = [
        {
            "id": switch["id"],
            "kind": switch["kind"],
            "from": _node_of(terminals, switch["terminal_from"]),
            "to": _node_of(terminals, switch["terminal_to"]),
            "initially_closed": switch["closed"],
        }
        for switch in network_dump["switches"]
    ]
    transformers = []
    for transformer in network_dump.get("transformers") or []:
        winding_nodes = []
        winding_kv = []
        for winding in transformer["windings"]:
            node_id = _node_of(terminals, winding["terminal_id"])
            winding_nodes.append(node_id)
            winding_kv.append(next(node["kv"] for node in nodes if node["id"] == node_id))
        transformers.append(
            {
                "id": transformer["id"],
                "group": transformer.get("vector_group") or "",
                "nodes": winding_nodes,
                "kvs": winding_kv,
            }
        )
    devices = []
    for load in network_dump.get("loads") or []:
        devices.append(
            {
                "id": load["id"],
                "node": _node_of(terminals, load["terminal_id"]),
                "kind": "load",
                "critical": bool(load.get("critical")),
            }
        )
    for generator in network_dump.get("generators") or []:
        devices.append({"id": generator["id"], "node": _node_of(terminals, generator["terminal_id"]), "kind": "generator"})
    for inverter in network_dump.get("inverters") or []:
        devices.append({"id": inverter["id"], "node": _node_of(terminals, inverter["terminal_id"]), "kind": "inverter"})
    for storage in network_dump.get("storages") or []:
        devices.append({"id": storage["id"], "node": _node_of(terminals, storage["terminal_id"]), "kind": "storage"})
    for shunt in network_dump.get("shunts") or []:
        devices.append({"id": shunt["id"], "node": _node_of(terminals, shunt["terminal_id"]), "kind": "shunt"})
    for source in network_dump.get("sources") or []:
        devices.append({"id": source["id"], "node": _node_of(terminals, source["terminal_id"]), "kind": "source"})
    for meter in network_dump.get("meters") or []:
        devices.append({"id": meter["id"], "node": meter["connectivity_node_id"], "kind": "meter"})
    bus_nodes = _bus_nodes(network_dump)
    public_steps = []
    for step in steps:
        closed = {item["id"]: item["closed"] for item in step["observation"].get("switches", [])}
        privileged = step.get("privileged") or {"source": "privileged", "voltages": [], "currents": []}
        public_steps.append(
            {
                "time_s": step["time_s"],
                "action": step["action"],
                "acceptance": step["acceptance"],
                "origin": step["origin"],
                "operations": step["operations"],
                "solve_status": step["solve_status"],
                "violations": step["violations"],
                "observation": step["observation"],
                "privileged": privileged,
                "switches": closed,
                "node_state": _node_state(bus_nodes, privileged),
            }
        )
    return {
        "name": network_dump.get("name") or network_dump.get("id") or "",
        "nodes": nodes,
        "lines": lines,
        "switches": switches,
        "transformers": transformers,
        "devices": devices,
        "steps": public_steps,
        "v_min_pu": v_min,
        "v_max_pu": v_max,
    }


def _bus_nodes(network_dump: dict[str, Any]) -> dict[str, list[str]]:
    try:
        network = Network.model_validate(network_dump)
        topology = compile_topology(network)
    except Exception:
        return {}
    grouped: dict[str, list[str]] = {}
    for node_id, bus_id in topology.node_to_bus.items():
        grouped.setdefault(bus_id, []).append(node_id)
    return grouped


def _node_state(bus_nodes: dict[str, list[str]], privileged: dict[str, Any]) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for voltage in privileged.get("voltages") or []:
        if voltage.get("phase") != "A":
            continue
        for node_id in bus_nodes.get(voltage.get("bus_id"), []):
            state[node_id] = {"v_pu": voltage.get("v_pu"), "energized": voltage.get("energized", True)}
    return state
