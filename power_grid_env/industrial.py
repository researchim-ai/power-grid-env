"""Небольшой промышленный контур 110/10/0.4 кВ с двумя вводами."""

from __future__ import annotations

from power_grid_env.build import NetBuilder
from power_grid_env.model import Network


def build_industrial_plant() -> Network:
    builder = NetBuilder("plant-110", "Площадка 110/10/0.4", 110.0, s_base_va=100e6)
    builder.node("grid110", 110)
    builder.node("bus110", 110)
    builder.source("grid110", 0.4, 2.0, source_id="infeed_110")
    builder.switch("grid110", "bus110", switch_id="q110", rated_current_a=800)
    builder.node("bus10", 10)
    builder.transformer(
        "bus110",
        "bus10",
        vector_group="YNyn0",
        kva=25000,
        hv_kv=110,
        lv_kv=10,
        x_pct=12,
        r_pct=0.6,
        transformer_id="t_110_10",
    )
    builder.node("section_b", 10)
    builder.line_z1("bus10", "section_b", 0.15 + 0.25j, rating_a=400, line_id="cable_10")
    builder.node("lv", 0.4)
    builder.transformer(
        "section_b",
        "lv",
        vector_group="Dyn11",
        kva=1600,
        hv_kv=10,
        lv_kv=0.4,
        x_pct=6,
        r_pct=1.0,
        hv_ground="not_applicable",
        lv_ground="solid",
        transformer_id="t_10_04",
    )
    builder.node("mcc", 0.4)
    builder.line_z1("lv", "mcc", 0.01 + 0.015j, rating_a=800, line_id="mcc_feeder")
    builder.load("mcc", 500e3, 180e3, load_id="process_load", critical=True)
    builder.node("reserve10", 10)
    builder.source("reserve10", 0.05, 0.3, source_id="infeed_10", is_slack=True)
    builder.switch("reserve10", "section_b", switch_id="ats", closed=False, rated_current_a=400)
    builder.meter("mcc", meter_id="meter_mcc")
    builder.meter("bus10", meter_id="meter_10")
    return builder.build()
