"""Рендер наблюдения. Подписи оборудования не попадают в режим только пикселей."""

from __future__ import annotations

from typing import Any


def observation_svg(observation: dict[str, Any], width: int = 480, height: int = 220) -> str:
    voltages = observation.get("voltages") or []
    bars = []
    for index, reading in enumerate(voltages[:12]):
        magnitude = max(0.0, min(1.5, float(reading["v_pu"])))
        x = 20 + (index % 6) * 70
        y = 160 - magnitude * 80
        color = "#8c1d18" if magnitude < observation.get("limits", {}).get("v_min_pu", 0.95) else "#16342c"
        bars.append(f'<rect x="{x}" y="{y:.1f}" width="24" height="{(160 - y):.1f}" fill="{color}"/>')
        bars.append(f'<text x="{x}" y="180" font-size="10">{reading["phase"]}</text>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="#f6f4ef"/>'
        '<text x="12" y="24" font-size="14">observation</text>'
        + "".join(bars)
        + "</svg>"
    )


def pixels_only_html(points: list[dict[str, float]] | None = None, width: int = 480, height: int = 220) -> str:
    """Холст без идентификаторов. Соответствие координат и оборудования остаётся снаружи."""
    drawn = points or []
    coords = ", ".join(f"[{float(point['x']):.1f}, {float(point['y']):.1f}]" for point in drawn)
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>pixels</title></head>
<body style="margin:0;background:#111">
<canvas id="c" width="{width}" height="{height}"></canvas>
<p id="hit"></p>
<script>
const points = [{coords}];
const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
ctx.fillStyle = "#123";
ctx.fillRect(0, 0, canvas.width, canvas.height);
ctx.strokeStyle = "#ddd";
ctx.fillStyle = "#6cf";
points.forEach(([x, y], index) => {{
  if (index) {{ ctx.beginPath(); ctx.moveTo(points[index - 1][0], points[index - 1][1]); ctx.lineTo(x, y); ctx.stroke(); }}
  ctx.beginPath(); ctx.arc(x, y, 8, 0, 6.3); ctx.fill();
}});
canvas.addEventListener("click", (event) => {{
  const rect = canvas.getBoundingClientRect();
  document.getElementById("hit").textContent = (event.clientX - rect.left).toFixed(1) + "," + (event.clientY - rect.top).toFixed(1);
}});
</script></body></html>
"""


def hit_test(x: float, y: float, points: list[dict[str, Any]] | None = None) -> str | None:
    for point in points or []:
        if (x - float(point["x"])) ** 2 + (y - float(point["y"])) ** 2 <= 12**2:
            return str(point["id"])
    return None
