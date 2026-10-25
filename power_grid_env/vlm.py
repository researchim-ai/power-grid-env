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


def pixels_only_html(width: int = 480, height: int = 220) -> str:
    """Холст без идентификаторов оборудования. Попадание разрешает внешний runner."""
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>pixels</title></head>
<body style="margin:0;background:#111">
<canvas id="c" width="{width}" height="{height}"></canvas>
<p id="hit"></p>
<script>
const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
ctx.fillStyle = "#123";
ctx.fillRect(0, 0, canvas.width, canvas.height);
ctx.strokeStyle = "#ddd";
ctx.beginPath(); ctx.moveTo(40, 120); ctx.lineTo(220, 80); ctx.lineTo(400, 140); ctx.stroke();
ctx.fillStyle = "#6cf";
[[40,120],[220,80],[400,140]].forEach(([x,y]) => {{ ctx.beginPath(); ctx.arc(x,y,8,0,6.3); ctx.fill(); }});
canvas.addEventListener("click", (event) => {{
  const rect = canvas.getBoundingClientRect();
  document.getElementById("hit").textContent = (event.clientX - rect.left).toFixed(1) + "," + (event.clientY - rect.top).toFixed(1);
}});
</script></body></html>
"""


def hit_test(x: float, y: float) -> str | None:
    points = {"source": (40, 120), "mid": (220, 80), "load": (400, 140)}
    for name, (px, py) in points.items():
        if (x - px) ** 2 + (y - py) ** 2 <= 12**2:
            return name
    return None
