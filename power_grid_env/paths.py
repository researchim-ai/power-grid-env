"""Каталог проекта и каталог установленного пакета.

Поддержанная среда v1 — Linux. Windows и macOS не заявлены.
Поиск корня репозитория нужен только командам разработки, у которых рядом
лежат cases/. Обычный расчёт и контроллер его не используют.
"""

from __future__ import annotations

import os
from pathlib import Path

SUPPORTED_SYSTEMS = ("Linux",)


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def project_dir() -> Path:
    """Каталог пользователя: POWER_GRID_PROJECT или текущий каталог."""
    chosen = os.environ.get("POWER_GRID_PROJECT")
    path = Path(chosen).expanduser().resolve() if chosen else Path.cwd().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "cases").exists():
            return parent
    raise FileNotFoundError(
        "корень разработки power-grid-env не найден. "
        "Для установленного пакета задайте POWER_GRID_PROJECT и передайте пути к модели явно."
    )
