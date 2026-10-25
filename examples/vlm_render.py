"""SVG только из observation и холст без идентификаторов."""

from pathlib import Path

from power_grid_env.importer import import_network
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine
from power_grid_env.vlm import observation_svg, pixels_only_html

network, _report = import_network(
    Path("cases/acceptance/feeder_north/feeder.dss"),
    Path("cases/acceptance/feeder_north/pge-import.yaml"),
)
scenario = Scenario(id="vlm", horizon_s=60, control_interval_s=60, settle_s=600)
engine = Engine(network, scenario)
svg = observation_svg(engine.reset())
Path("reports/observation.svg").write_text(svg, encoding="utf-8")
Path("reports/pixels_only.html").write_text(pixels_only_html(), encoding="utf-8")
print("reports/observation.svg", "reports/pixels_only.html")
