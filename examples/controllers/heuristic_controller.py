"""Внешний контроллер эвристики. Движок вызывает его отдельным процессом."""

from power_grid_env.controllers import serve
from power_grid_env.heuristic import choose_action


def policy(observation, public, history):
    return choose_action(observation, public["network"], respect_rating=True)


if __name__ == "__main__":
    serve(policy)
