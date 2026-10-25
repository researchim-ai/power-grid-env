"""Baseline бездействия."""

from power_grid_env.controllers import serve


def policy(observation, public, history):
    return {"kind": "hold", "rationale": "бездействие"}


if __name__ == "__main__":
    serve(policy)
