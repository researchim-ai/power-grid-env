"""Типизированные инструменты LLM. Истина сценария и будущие аварии недоступны."""

from __future__ import annotations

from typing import Any

from power_grid_env.model import Network
from power_grid_env.simulation import Engine

TOOL_SCHEMAS = [
    {"name": "get_observation", "parameters": {}},
    {"name": "inspect_equipment", "parameters": {"equipment_id": "string"}},
    {"name": "query_allowed_history", "parameters": {}},
    {"name": "read_procedure", "parameters": {}},
    {"name": "simulate_plan", "parameters": {"commands": "list"}},
    {"name": "submit_plan", "parameters": {"action": "object"}},
]


class ToolSession:
    def __init__(self, engine: Engine, agent_network: Network) -> None:
        self.engine = engine
        self.agent_network = agent_network
        self.submitted: dict[str, Any] | None = None

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        if name == "get_observation":
            return {"ok": True, "observation": self.engine.observation()}
        if name == "inspect_equipment":
            return self.inspect_equipment(arguments["equipment_id"])
        if name == "query_allowed_history":
            return {"ok": True, "history": [{"time_s": item["time_s"], "action": item["action"]} for item in self.engine.history]}
        if name == "read_procedure":
            return {
                "ok": True,
                "procedure": "Изолировать известный повреждённый участок с двух сторон, затем подать питание только по разрешённой связи.",
            }
        if name == "simulate_plan":
            return self.engine.what_if(arguments.get("commands") or [], model=self.agent_network)
        if name == "submit_plan":
            action = arguments.get("action") or {"kind": "hold"}
            self.submitted = action
            return {"ok": True, "accepted_for_execution": False, "action": action}
        return {"ok": False, "error": "unknown_tool"}

    def inspect_equipment(self, equipment_id: str) -> dict[str, Any]:
        dump = self.agent_network.model_dump(mode="json")
        for key in ("lines", "switches", "loads", "transformers", "sources"):
            for item in dump[key]:
                if item["id"] == equipment_id:
                    public = {field: item[field] for field in item if field not in {"provenance"}}
                    return {"ok": True, "kind": key, "equipment": public}
        return {"ok": False, "error": "unknown_equipment"}
