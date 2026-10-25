"""Корень репозитория и каталоги артефактов."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "cases").exists():
            return parent
    raise FileNotFoundError("корень power-grid-env не найден")
