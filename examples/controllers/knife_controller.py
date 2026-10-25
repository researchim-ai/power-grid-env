"""Пытается отключить разъединитель под током."""

from power_grid_env.controllers import serve


def policy(observation, public, history):
    for switch in observation.get("switches", []):
        if switch["kind"] == "disconnector" and switch["closed"]:
            return {"kind": "switch", "equipment_id": switch["id"], "command": "open", "rationale": "ошибочное отключение"}
    return {"kind": "hold"}


if __name__ == "__main__":
    serve(policy)
