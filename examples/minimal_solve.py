"""Минимальный расчёт двухузловой схемы через phase-domain backend."""

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder

builder = NetBuilder("minimal", "minimal", 12.47)
builder.node("src")
builder.node("load")
builder.source("src", 1e-3, 1e-3)
builder.line_z1("src", "load", 0.2 + 0.4j, line_id="line")
builder.load("load", 200e3, 40e3, load_id="load")
result = solve_network(builder.build())
print(result.status, result.iterations)
for voltage in result.voltages:
    if voltage.phase == "A":
        print(voltage.bus_id, abs(voltage.complex))
