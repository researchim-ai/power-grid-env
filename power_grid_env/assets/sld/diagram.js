let mode = "privileged";
let index = data.steps.length ? data.steps.length - 1 : 0;
const banner = document.getElementById("banner");
const schemeName = document.getElementById("scheme-name");
if (schemeName) schemeName.textContent = data.name || "";

function step() { return data.steps[index]; }

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

function kvColor(kv, dead) {
  if (dead) return "#a8a29e";
  const value = Number(kv || 0);
  if (value >= 35) return "#9f1239";
  if (value >= 3) return "#b45309";
  return "#15803d";
}

function kvLabel(kv) {
  const value = Number(kv || 0);
  if (!value) return "";
  if (value >= 10) return `${value.toFixed(value >= 100 ? 0 : 0)} кВ`;
  if (value >= 1) return `${value.toFixed(1)} кВ`;
  return `${Math.round(value * 1000)} В`;
}

function layout() {
  const placed = {};
  (data.nodes || []).forEach((node, i) => {
    placed[node.id] = {
      x: node.x == null ? 90 + (i % 4) * 220 : node.x,
      y: node.y == null ? 120 + Math.floor(i / 4) * 160 : node.y,
      kv: node.kv,
    };
  });
  const ids = Object.keys(placed);
  let minDistance = Infinity;
  for (let i = 0; i < ids.length; i += 1) {
    for (let j = i + 1; j < ids.length; j += 1) {
      const distance = Math.hypot(placed[ids[i]].x - placed[ids[j]].x, placed[ids[i]].y - placed[ids[j]].y);
      if (distance > 1 && distance < minDistance) minDistance = distance;
    }
  }
  const scale = minDistance < 200 ? 200 / minDistance : 1;
  if (scale === 1) return placed;
  const xs = ids.map((id) => placed[id].x);
  const ys = ids.map((id) => placed[id].y);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const spread = {};
  ids.forEach((id) => {
    spread[id] = { ...placed[id], x: cx + (placed[id].x - cx) * scale, y: cy + (placed[id].y - cy) * scale };
  });
  return spread;
}

function nodeState(id) {
  const current = step();
  if (!current) return null;
  if (mode === "observation") {
    const reading = ((current.observation || {}).voltages || []).find((item) => item.node_id === id && item.phase === "A");
    return reading ? { v_pu: Number(reading.v_pu), energized: true, measured: true } : null;
  }
  return (current.node_state || {})[id] || null;
}

function deadNode(id) {
  const state = nodeState(id);
  return !!(state && state.energized === false);
}

function preferDirection(id, positions, options) {
  const origin = positions[id];
  for (const direction of options) {
    const blocked = Object.values(positions).some((point) => {
      if (point === origin) return false;
      const vx = point.x - origin.x;
      const vy = point.y - origin.y;
      return vx * direction.x + vy * direction.y > 50 && Math.abs(vx * direction.y - vy * direction.x) < 100;
    });
    if (!blocked) return direction;
  }
  return outward(id, positions);
}

function outward(id, positions) {
  const origin = positions[id];
  let sx = 0;
  let sy = 0;
  let count = 0;
  (data.lines || []).concat(data.switches || []).forEach((edge) => {
    const other = edge.from === id ? edge.to : edge.to === id ? edge.from : null;
    if (!other || !positions[other]) return;
    sx += positions[other].x - origin.x;
    sy += positions[other].y - origin.y;
    count += 1;
  });
  (data.transformers || []).forEach((item) => {
    if (!(item.nodes || []).includes(id)) return;
    item.nodes.forEach((other) => {
      if (other === id || !positions[other]) return;
      sx += positions[other].x - origin.x;
      sy += positions[other].y - origin.y;
      count += 1;
    });
  });
  if (!count) return { x: 0, y: 1 };
  const length = Math.hypot(sx, sy);
  if (length < 1e-6) return { x: 0, y: 1 };
  return { x: -sx / length, y: -sy / length };
}

function conductor(a, b, inset, gap) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = Math.hypot(dx, dy) || 1;
  const ux = dx / length;
  const uy = dy / length;
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  return {
    x1: a.x + ux * inset,
    y1: a.y + uy * inset,
    x2: mid.x - ux * gap,
    y2: mid.y - uy * gap,
    x3: mid.x + ux * gap,
    y3: mid.y + uy * gap,
    x4: b.x - ux * inset,
    y4: b.y - uy * inset,
    mid,
    deg: Math.atan2(dy, dx) * 180 / Math.PI,
  };
}

function useSymbol(id) {
  return `<use href="#${id}" x="-32" y="-32" width="64" height="64"/>`;
}

function line(x1, y1, x2, y2, color, dashed) {
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="2.4" stroke-linecap="round"${dashed ? ' stroke-dasharray="7 5"' : ""}/>`;
}

function halo() {
  return `<circle class="halo" r="30" fill="none" stroke="#b45309" stroke-width="2"/>`;
}

function label(x, y, text, extra) {
  return `<text x="${x}" y="${y}" text-anchor="middle" font-size="12" fill="#292524"${extra || ""}>${esc(text)}</text>`;
}

function alarmSet() {
  const current = step();
  return new Set(((current && current.violations) || []).map((item) => item.equipment_id));
}

function render() {
  const current = step();
  banner.textContent = mode === "privileged"
    ? "привилегированный вид оценки — эти величины не передаются контроллеру"
    : "observation — только то, что получил контроллер";
  drawScene();
  const timeline = document.getElementById("timeline");
  timeline.innerHTML = data.steps.map((item, i) => {
    const action = item.action || {};
    const name = action.kind === "switch" ? `${action.command} ${action.equipment_id}` : action.kind;
    return `<button type="button" data-i="${i}">t=${item.time_s}s ${esc(name)} [${esc(item.origin)}]</button>`;
  }).join("");
  timeline.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => { index = Number(button.dataset.i); render(); }));
  const detail = document.getElementById("detail");
  if (!current) { detail.textContent = "нет шагов"; return; }
  const rows = [];
  if (mode === "privileged") {
    (current.privileged.voltages || []).filter((item) => item.phase === "A").forEach((item) => {
      const bad = item.energized && (item.v_pu < data.v_min_pu || item.v_pu > data.v_max_pu);
      rows.push(`<tr class="${bad ? "bad" : ""}"><td>${esc(item.bus_id)} A</td><td>${Number(item.v_pu).toFixed(4)} о.е.</td><td>${data.v_min_pu}…${data.v_max_pu}</td></tr>`);
    });
    (current.privileged.currents || []).forEach((item) => {
      const bad = item.current_a > item.rating_a;
      rows.push(`<tr class="${bad ? "bad" : ""}"><td>${esc(item.equipment_id)}</td><td>${Number(item.current_a).toFixed(2)} А</td><td>${item.rating_a} А</td></tr>`);
    });
  } else {
    (current.observation.voltages || []).filter((item) => item.phase === "A").forEach((item) => {
      rows.push(`<tr><td>${esc(item.node_id)} A</td><td>${Number(item.v_pu).toFixed(4)} о.е.</td><td>измерение</td></tr>`);
    });
  }
  const violations = (current.violations || []).map((item) => `<li><button type="button" data-eq="${esc(item.equipment_id)}">${esc(item.kind)}: ${esc(item.equipment_id)} = ${Number(item.value).toFixed(3)} ${esc(item.unit)}, предел ${esc(item.limit)}</button></li>`).join("");
  const accepted = current.acceptance ? current.acceptance.accepted : false;
  const reason = current.acceptance ? (current.acceptance.reason || "") : "";
  const executed = current.acceptance && current.acceptance.executed_value != null ? current.acceptance.executed_value : "—";
  detail.innerHTML = `<p>Шаг t=${current.time_s}s, статус расчёта ${esc(current.solve_status)}.</p>
  <table><tr><th>Объект</th><th>Значение</th><th>Предел</th></tr>${rows.join("")}</table>
  <h4>Нарушения</h4><ul>${violations || "<li>нет</li>"}</ul>
  <p>Предложено: ${esc(JSON.stringify(current.action))}. Принято: ${accepted}. Причина: ${esc(reason)}. Исполнено: ${esc(executed)}. Операций за шаг: ${esc(current.operations)}.</p>`;
  const series = data.steps.map((item) => {
    const pool = mode === "privileged" ? ((item.privileged && item.privileged.voltages) || []) : ((item.observation && item.observation.voltages) || []);
    const values = pool.filter((reading) => reading.phase === "A").map((reading) => Number(reading.v_pu));
    return values.length ? Math.min(...values) : null;
  });
  const chart = document.getElementById("chart");
  const finite = series.filter((value) => value != null);
  if (!finite.length) chart.textContent = "нет ряда напряжения";
  else {
    const width = 240;
    const height = 80;
    const min = Math.min(...finite, data.v_min_pu);
    const max = Math.max(...finite, data.v_max_pu);
    const span = Math.max(1e-6, max - min);
    const coords = series.map((value, i) => {
      if (value == null) return "";
      const x = 8 + (series.length === 1 ? 0 : i * (width - 16) / (series.length - 1));
      const y = 8 + (max - value) / span * (height - 16);
      return `${x},${y}`;
    }).filter(Boolean);
    const dots = coords.map((pair) => {
      const [x, y] = pair.split(",");
      return `<circle cx="${x}" cy="${y}" r="3" fill="#16342c"/>`;
    }).join("");
    chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="100%" height="80"><polyline fill="none" stroke="#16342c" stroke-width="2" points="${coords.join(" ")}"/>${dots}</svg><p>${finite[finite.length - 1].toFixed(4)} о.е.</p>`;
  }
  detail.querySelectorAll("button[data-eq]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".selected").forEach((node) => node.classList.remove("selected"));
    const raw = button.dataset.eq || "";
    const target = document.querySelector(`[data-id="${raw}"]`) || document.querySelector(`[data-id="${raw.split("__")[0]}"]`);
    if (target) target.classList.add("selected");
  }));
}

function drawScene() {
  const positions = layout();
  const alarms = alarmSet();
  const bounds = [];
  const mark = (x, y) => bounds.push({ x, y });
  Object.values(positions).forEach((point) => mark(point.x, point.y));
  const parts = [];

  (data.lines || []).forEach((edge) => {
    const a = positions[edge.from];
    const b = positions[edge.to];
    if (!a || !b) return;
    const dead = deadNode(edge.from) || deadNode(edge.to);
    const color = alarms.has(edge.id) ? "#9f1239" : kvColor(a.kv, dead);
    const path = conductor(a, b, 24, 0);
    parts.push(line(path.x1, path.y1, path.x4, path.y4, color, dead));
    parts.push(`<g data-id="${esc(edge.id)}" class="${alarms.has(edge.id) ? "alarm" : ""}" transform="translate(${path.mid.x} ${path.mid.y})">${halo()}</g>`);
    const length = Math.hypot(b.x - a.x, b.y - a.y);
    if (length > 150) {
      parts.push(label(path.mid.x, path.mid.y - 10, edge.id));
      mark(path.mid.x, path.mid.y - 16);
    }
  });

  (data.switches || []).forEach((edge) => {
    const a = positions[edge.from];
    const b = positions[edge.to];
    if (!a || !b) return;
    const current = step();
    const closed = current && current.switches && Object.prototype.hasOwnProperty.call(current.switches, edge.id)
      ? current.switches[edge.id]
      : edge.initially_closed;
    const dead = deadNode(edge.from) || deadNode(edge.to);
    const color = alarms.has(edge.id) ? "#9f1239" : kvColor(a.kv, dead);
    const path = conductor(a, b, 24, 32);
    const along = path.deg * Math.PI / 180;
    const labelX = path.mid.x - Math.sin(along) * 30;
    const labelY = path.mid.y + Math.cos(along) * 30;
    const symbol = edge.kind === "disconnector"
      ? (closed ? "sld-disconnector-closed" : "sld-disconnector-open")
      : edge.kind === "fuse"
        ? "sld-fuse"
        : (closed ? "sld-breaker-closed" : "sld-breaker-open");
    const state = closed ? "вкл" : "откл";
    parts.push(line(path.x1, path.y1, path.x2, path.y2, color, !closed || dead));
    parts.push(line(path.x3, path.y3, path.x4, path.y4, color, !closed || dead));
    parts.push(`<g data-id="${esc(edge.id)}" class="${alarms.has(edge.id) ? "alarm" : ""}" style="color:${color}" transform="translate(${path.mid.x} ${path.mid.y}) rotate(${path.deg})">${halo()}${useSymbol(symbol)}</g>`);
    parts.push(label(labelX, labelY, `${edge.id} · ${state}`));
    mark(labelX, labelY);
  });

  (data.transformers || []).forEach((item) => {
    const nodes = (item.nodes || []).map((id) => positions[id]).filter(Boolean);
    if (nodes.length < 2) return;
    const dead = (item.nodes || []).some((id) => deadNode(id));
    if (nodes.length === 2) {
      const path = conductor(nodes[0], nodes[1], 24, 30);
      const left = kvColor(item.kvs[0], dead);
      const right = kvColor(item.kvs[1] == null ? item.kvs[0] : item.kvs[1], dead);
      parts.push(line(path.x1, path.y1, path.x2, path.y2, left, dead));
      parts.push(line(path.x3, path.y3, path.x4, path.y4, right, dead));
      const dx = nodes[1].x - nodes[0].x;
      const dy = nodes[1].y - nodes[0].y;
      const length = Math.hypot(dx, dy) || 1;
      const ux = dx / length;
      const uy = dy / length;
      parts.push(`<g data-id="${esc(item.id)}" class="${alarms.has(item.id) ? "alarm" : ""}" transform="translate(${path.mid.x} ${path.mid.y})">${halo()}
        <circle cx="${-ux * 8}" cy="${-uy * 8}" r="13" fill="#fffdf8" stroke="${left}" stroke-width="2.3"/>
        <circle cx="${ux * 8}" cy="${uy * 8}" r="13" fill="#fffdf8" stroke="${right}" stroke-width="2.3"/>
      </g>`);
      parts.push(label(path.mid.x, path.mid.y + 30, item.id));
      parts.push(`<text x="${path.mid.x}" y="${path.mid.y + 44}" text-anchor="middle" font-size="11" fill="#57534e">${esc(item.group || "")}</text>`);
      mark(path.mid.x, path.mid.y + 48);
    } else {
      const center = {
        x: nodes.reduce((sum, node) => sum + node.x, 0) / nodes.length,
        y: nodes.reduce((sum, node) => sum + node.y, 0) / nodes.length,
      };
      nodes.forEach((node, winding) => {
        const color = kvColor(item.kvs[winding], dead);
        const dx = center.x - node.x;
        const dy = center.y - node.y;
        const length = Math.hypot(dx, dy) || 1;
        parts.push(line(node.x + dx / length * 24, node.y + dy / length * 24, center.x - dx / length * 20, center.y - dy / length * 20, color, dead));
      });
      const coils = nodes.map((_, winding) => {
        const angle = -Math.PI / 2 + winding * 2 * Math.PI / nodes.length;
        const color = kvColor(item.kvs[winding], dead);
        return `<circle cx="${Math.cos(angle) * 11}" cy="${Math.sin(angle) * 11}" r="12" fill="#fffdf8" stroke="${color}" stroke-width="2.2"/>`;
      }).join("");
      parts.push(`<g data-id="${esc(item.id)}" class="${alarms.has(item.id) ? "alarm" : ""}" transform="translate(${center.x} ${center.y})">${halo()}${coils}</g>`);
      parts.push(label(center.x, center.y + 36, `${item.id} ${item.group || ""}`));
      mark(center.x, center.y + 40);
    }
  });

  const grouped = {};
  (data.devices || []).filter((item) => item.kind !== "meter").forEach((item) => {
    (grouped[item.node] ||= []).push(item);
  });
  const placeDevices = (id, list, direction) => {
    const origin = positions[id];
    list.forEach((item, slot) => {
      const distance = 96 + slot * 92;
      const center = { x: origin.x + direction.x * distance, y: origin.y + direction.y * distance };
      const tip = { x: origin.x + direction.x * (distance - 30), y: origin.y + direction.y * (distance - 30) };
      const color = kvColor(origin.kv, deadNode(id));
      const symbol = {
        load: "sld-load",
        generator: "sld-generator",
        inverter: "sld-pv",
        storage: "sld-storage",
        shunt: "sld-capacitor",
        source: "sld-grid",
      }[item.kind] || "sld-load";
      const rotation = Math.atan2(-direction.x, direction.y) * 180 / Math.PI;
      const caption = item.critical ? `${item.id} · критическая` : item.id;
      parts.push(line(origin.x + direction.x * 18, origin.y + direction.y * 18, tip.x, tip.y, color, deadNode(id)));
      parts.push(`<g data-id="${esc(item.id)}" class="${alarms.has(item.id) ? "alarm" : ""}" style="color:${color}" transform="translate(${center.x} ${center.y}) rotate(${rotation})">${halo()}${useSymbol(symbol)}</g>`);
      const textAt = { x: center.x + direction.x * 46, y: center.y + direction.y * 46 };
      parts.push(label(textAt.x, textAt.y, caption));
      mark(textAt.x, textAt.y);
      mark(center.x, center.y);
    });
  };
  Object.entries(grouped).forEach(([id, list]) => {
    if (!positions[id]) return;
    const sources = list.filter((item) => item.kind === "source");
    const rest = list.filter((item) => item.kind !== "source");
    if (sources.length) placeDevices(id, sources, preferDirection(id, positions, [{ x: -1, y: 0 }, { x: 0, y: -1 }]));
    if (rest.length) placeDevices(id, rest, preferDirection(id, positions, [{ x: 0, y: 1 }, { x: 0, y: -1 }]));
  });

  (data.devices || []).filter((item) => item.kind === "meter").forEach((item, slot) => {
    const origin = positions[item.node];
    if (!origin) return;
    const center = { x: origin.x + 42 + slot * 24, y: origin.y - 18 };
    const color = kvColor(origin.kv, false);
    parts.push(`<g data-id="${esc(item.id)}" style="color:${color}" transform="translate(${center.x} ${center.y}) scale(0.5)">${halo()}${useSymbol("sld-meter")}</g>`);
    mark(center.x, center.y);
  });

  Object.entries(positions).forEach(([id, point]) => {
    const state = nodeState(id);
    const dead = !!(state && state.energized === false);
    const color = kvColor(point.kv, dead);
    const bad = state && state.energized && (state.v_pu < data.v_min_pu || state.v_pu > data.v_max_pu);
    const hungDown = outward(id, positions).y > 0.35;
    const voltageY = hungDown ? point.y - 44 : point.y + 22;
    parts.push(`<g data-id="${esc(id)}">
      <line x1="${point.x - 28}" y1="${point.y}" x2="${point.x + 28}" y2="${point.y}" stroke="${color}" stroke-width="6" stroke-linecap="round"${dead ? ' stroke-dasharray="2 7"' : ""}/>
      <text x="${point.x}" y="${point.y - 16}" text-anchor="middle" font-size="13" font-weight="600" fill="#1c1917">${esc(id)}</text>
      <text x="${point.x}" y="${point.y - 30}" text-anchor="middle" font-size="11" fill="${color}">${esc(kvLabel(point.kv))}</text>
      ${state && state.v_pu != null ? `<text x="${point.x}" y="${voltageY}" text-anchor="middle" font-size="11" fill="${bad ? "#9f1239" : "#44403c"}">${state.v_pu.toFixed(3)} о.е.</text>` : ""}
    </g>`);
    mark(point.x, point.y - 52);
    mark(point.x, point.y + 28);
  });

  const scene = document.getElementById("scene");
  const svg = document.getElementById("diagram");
  scene.innerHTML = parts.join("");
  if (!bounds.length) return;
  const xs = bounds.map((point) => point.x);
  const ys = bounds.map((point) => point.y);
  const minX = Math.min(...xs) - 48;
  const minY = Math.min(...ys) - 36;
  const width = Math.max(...xs) - minX + 64;
  const height = Math.max(...ys) - minY + 48;
  svg.setAttribute("viewBox", `${minX} ${minY} ${width} ${height}`);
  document.getElementById("paper").setAttribute("x", minX);
  document.getElementById("paper").setAttribute("y", minY);
  document.getElementById("paper").setAttribute("width", width);
  document.getElementById("paper").setAttribute("height", height);
}

function drawLegend() {
  const items = [
    ["sld-breaker-closed", "выключатель включён"],
    ["sld-breaker-open", "выключатель отключён"],
    ["sld-disconnector-open", "разъединитель отключён"],
    ["sld-transformer-2", "трансформатор"],
    ["sld-load", "нагрузка"],
    ["sld-generator", "генератор"],
    ["sld-grid", "ввод"],
    ["sld-capacitor", "конденсатор"],
    ["sld-storage", "накопитель"],
    ["sld-pv", "фотоэлектрическая установка"],
    ["sld-meter", "измерение"],
  ];
  const symbols = items.map(([id, name]) => `<span><svg viewBox="-32 -32 64 64"><use href="#${id}" x="-32" y="-32" width="64" height="64"/></svg>${name}</span>`).join("");
  document.getElementById("legend").innerHTML = `${symbols}<span class="swatch hv">110 кВ и выше</span><span class="swatch mv">6–35 кВ</span><span class="swatch lv">ниже 6 кВ</span>`;
}

document.getElementById("priv").addEventListener("click", () => { mode = "privileged"; render(); });
document.getElementById("obs").addEventListener("click", () => { mode = "observation"; render(); });
drawLegend();
render();
