"""Завершает процесс на первом действии."""

import sys

from power_grid_env.controllers import serve


def policy(observation, public, history):
    sys.exit(3)


if __name__ == "__main__":
    serve(policy)
