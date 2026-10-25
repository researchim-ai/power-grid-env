"""Намеренно превышает decision timeout."""

import time

from power_grid_env.controllers import serve


def policy(observation, public, history):
    time.sleep(30)
    return {"kind": "hold"}


if __name__ == "__main__":
    serve(policy)
