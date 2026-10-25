"""Минимальный инженерный просмотр: схема, шкала времени, пределы и действия.

Привилегированные величины и наблюдение контроллера подписаны и не смешиваются.
Страница самодостаточна и не загружает ресурсы из сети.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_viewer(path: Path, bundle: dict[str, Any]) -> None:
    payload = json.dumps(bundle, ensure_ascii=False).replace("<", "\\u003c")
    path.write_text(_PAGE.replace("__DATA__", payload), encoding="utf-8")


_PAGE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Просмотр испытания</title>
<style>
body { margin: 0; font-family: sans-serif; color: #17211c; background: #f6f4ef; }
header { background: #16342c; color: white; padding: 12px 16px; display: flex; justify-content: space-between; }
.banner { font-weight: 700; }
main { display: grid; grid-template-columns: 280px 1fr 320px; gap: 12px; padding: 12px; }
section { background: white; border: 1px solid #ddd; padding: 8px; min-height: 240px; }
svg { width: 100%; height: 420px; background: #fbfaf7; }
.bad { color: #8c1d18; font-weight: 700; }
button { margin: 2px 0; display: block; }
.selected { outline: 3px solid #b45309; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
td, th { border-bottom: 1px solid #eee; padding: 4px; text-align: left; }
</style></head><body>
<header>
  <div class="banner" id="banner">привилегированный вид оценки</div>
  <div>
    <button id="priv" type="button">привилегированный вид</button>
    <button id="obs" type="button">только observation</button>
  </div>
</header>
<main>
  <section><h3>Время и действия</h3><div id="timeline"></div></section>
  <section><h3>Схема</h3><svg id="diagram"></svg></section>
  <section><h3>Величины и нарушения</h3><div id="detail"></div></section>
</main>
<script>
const data = __DATA__;
let mode = "privileged";
let index = data.steps.length ? data.steps.length - 1 : 0;
const banner = document.getElementById("banner");
function step() { return data.steps[index]; }
function layout() {
  const nodes = data.nodes;
  if (!nodes.length) return {};
  const placed = {};
  nodes.forEach((node, i) => {
    placed[node.id] = { x: node.x == null ? 80 + (i % 4) * 140 : node.x, y: node.y == null ? 60 + Math.floor(i / 4) * 90 : node.y };
  });
  return placed;
}
function render() {
  const current = step();
  banner.textContent = mode === "privileged"
    ? "привилегированный вид оценки — эти величины не передаются контроллеру"
    : "observation — только то, что получил контроллер";
  const positions = layout();
  const svg = document.getElementById("diagram");
  const lines = data.lines.map(line => {
    const a = positions[line["from"]]; const b = positions[line["to"]];
    if (!a || !b) return "";
    return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#355" stroke-width="2"/>`;
  }).join("");
  const switches = data.switches.map(sw => {
    const a = positions[sw.from]; const b = positions[sw.to];
    if (!a || !b) return "";
    const closed = current && current.switches ? current.switches[sw.id] : sw.initially_closed;
    const mx = (a.x + b.x) / 2; const my = (a.y + b.y) / 2;
    return `<rect class="sw" data-id="${sw.id}" x="${mx-8}" y="${my-8}" width="16" height="16" fill="${closed ? "#16342c" : "#fff"}" stroke="#16342c"/>`;
  }).join("");
  const circles = Object.entries(positions).map(([id, p]) => `<circle cx="${p.x}" cy="${p.y}" r="7" fill="#4c6b52"/><text x="${p.x+10}" y="${p.y-10}" font-size="11">${id}</text>`).join("");
  svg.innerHTML = lines + switches + circles;
  const timeline = document.getElementById("timeline");
  timeline.innerHTML = data.steps.map((item, i) => {
    const action = item.action || {};
    const label = action.kind === "switch" ? `${action.command} ${action.equipment_id}` : action.kind;
    return `<button type="button" data-i="${i}">t=${item.time_s}s ${label} [${item.origin}]</button>`;
  }).join("");
  timeline.querySelectorAll("button").forEach(button => button.addEventListener("click", () => { index = Number(button.dataset.i); render(); }));
  const detail = document.getElementById("detail");
  if (!current) { detail.textContent = "нет шагов"; return; }
  const rows = [];
  if (mode === "privileged") {
    (current.privileged.voltages || []).filter(v => v.phase === "A").forEach(v => {
      const bad = v.energized && (v.v_pu < data.v_min_pu || v.v_pu > data.v_max_pu);
      rows.push(`<tr class="${bad ? "bad" : ""}"><td>${v.bus_id} A</td><td>${v.v_pu.toFixed(4)} о.е.</td><td>${data.v_min_pu}…${data.v_max_pu}</td></tr>`);
    });
    (current.privileged.currents || []).forEach(v => {
      const bad = v.current_a > v.rating_a;
      rows.push(`<tr class="${bad ? "bad" : ""}"><td>${v.equipment_id}</td><td>${v.current_a.toFixed(2)} А</td><td>${v.rating_a} А</td></tr>`);
    });
  } else {
    (current.observation.voltages || []).filter(v => v.phase === "A").forEach(v => {
      rows.push(`<tr><td>${v.node_id} A</td><td>${Number(v.v_pu).toFixed(4)} о.е.</td><td>измерение</td></tr>`);
    });
  }
  const violations = (current.violations || []).map(v => `<li><button type="button" data-eq="${v.equipment_id}">${v.kind}: ${v.equipment_id} = ${Number(v.value).toFixed(3)} ${v.unit}, предел ${v.limit}</button></li>`).join("");
  detail.innerHTML = `<p>Шаг t=${current.time_s}s, статус расчёта ${current.solve_status}.</p>
  <table><tr><th>Объект</th><th>Значение</th><th>Предел</th></tr>${rows.join("")}</table>
  <h4>Нарушения</h4><ul>${violations || "<li>нет</li>"}</ul>
  <p>Предложено: ${JSON.stringify(current.action)}. Принято: ${current.acceptance && current.acceptance.accepted}. Исполненные операции шага: ${current.operations}.</p>`;
  detail.querySelectorAll("button[data-eq]").forEach(button => button.addEventListener("click", () => {
    const id = button.dataset.eq;
    document.querySelectorAll(".selected").forEach(node => node.classList.remove("selected"));
    const target = document.querySelector(`[data-id="${id}"]`);
    if (target) target.classList.add("selected");
  }));
}
document.getElementById("priv").addEventListener("click", () => { mode = "privileged"; render(); });
document.getElementById("obs").addEventListener("click", () => { mode = "observation"; render(); });
render();
</script></body></html>
"""


def viewer_bundle(network_dump: dict[str, Any], steps: list[dict[str, Any]], v_min: float, v_max: float) -> dict[str, Any]:
    terminals = {item["id"]: item for item in network_dump["terminals"]}
    nodes = [
        {"id": node["id"], "x": node.get("x"), "y": node.get("y")}
        for node in network_dump["connectivity_nodes"]
    ]
    lines = []
    for line in network_dump["lines"]:
        lines.append(
            {
                "id": line["id"],
                "from": terminals[line["terminal_from"]]["connectivity_node_id"],
                "to": terminals[line["terminal_to"]]["connectivity_node_id"],
            }
        )
    switches = []
    for switch in network_dump["switches"]:
        switches.append(
            {
                "id": switch["id"],
                "from": terminals[switch["terminal_from"]]["connectivity_node_id"],
                "to": terminals[switch["terminal_to"]]["connectivity_node_id"],
                "initially_closed": switch["closed"],
            }
        )
    public_steps = []
    for step in steps:
        closed = {item["id"]: item["closed"] for item in step["observation"].get("switches", [])}
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
                "privileged": step.get("privileged") or {"source": "privileged", "voltages": [], "currents": []},
                "switches": closed,
            }
        )
    return {"nodes": nodes, "lines": lines, "switches": switches, "steps": public_steps, "v_min_pu": v_min, "v_max_pu": v_max}
