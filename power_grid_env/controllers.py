"""Контракт контроллера и адаптеры.

Subprocess не является песочницей недоверенного кода: он только отделяет
протокол. Изоляция прав, файлов и сети для недоверенного кода сюда не входит.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from typing import Any, Callable

from power_grid_env.ipc import (
    FrameTimeout,
    FrameTooLarge,
    ProtocolError,
    kill_process_group,
    parse_object,
    read_frame,
    write_frame,
)
from power_grid_env.paths import package_dir, project_dir


ACTION_SCHEMA = {
    "type": "object",
    "required": ["kind"],
    "properties": {
        "kind": {"enum": ["hold", "switch", "setpoint"]},
        "equipment_id": {"type": "string"},
        "command": {"enum": ["open", "close"]},
        "value": {"type": "number"},
        "unit": {"type": "string"},
    },
}

OBSERVATION_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "time_s", "source", "switches", "voltages", "action_mask"],
    "properties": {"source": {"const": "observation"}},
}


class InProcessController:
    def __init__(self, policy: Callable[[dict[str, Any], dict[str, Any], list], dict[str, Any]]) -> None:
        self.policy = policy
        self.public: dict[str, Any] = {}
        self.sent: list[dict[str, Any]] = []

    def initialize(self, observation_schema, action_schema, public_task_config) -> None:
        self.public = public_task_config
        self.sent.append({"op": "initialize", "public_task_config": public_task_config})

    def reset(self, public_episode_context) -> None:
        self.sent.append({"op": "reset", "public_episode_context": public_episode_context})

    def act(self, observation, permitted_history) -> dict[str, Any]:
        self.sent.append({"op": "act", "observation": observation, "permitted_history": permitted_history})
        action = self.policy(observation, self.public, permitted_history)
        return _normalize(action)

    def close(self) -> None:
        return None


class SubprocessController:
    def __init__(self, command: list[str], timeout_s: float = 2.0) -> None:
        self.command = command
        self.timeout_s = timeout_s
        self.sent: list[dict[str, Any]] = []
        env = os.environ.copy()
        work = str(project_dir())
        installed = str(package_dir().parent)
        env["PYTHONPATH"] = installed + os.pathsep + env.get("PYTHONPATH", "")
        env["POWER_GRID_PROJECT"] = work
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work,
            env=env,
            start_new_session=True,
        )
        self.alive = True

    def initialize(self, observation_schema, action_schema, public_task_config) -> dict[str, Any]:
        return self._request(
            {
                "op": "initialize",
                "observation_schema": observation_schema,
                "action_schema": action_schema,
                "public_task_config": public_task_config,
            }
        )

    def reset(self, public_episode_context) -> dict[str, Any]:
        return self._request({"op": "reset", "public_episode_context": public_episode_context})

    def act(self, observation, permitted_history) -> dict[str, Any]:
        response = self._request(
            {"op": "act", "observation": observation, "permitted_history": permitted_history}
        )
        if response["outcome"] != "ok":
            return response
        data = response["data"]
        action = data.get("action")
        if not isinstance(action, dict) or action.get("kind") not in {"hold", "switch", "setpoint"}:
            return {"outcome": "invalid_action", "data": data}
        if action["kind"] == "switch" and action.get("command") not in {"open", "close"}:
            return {"outcome": "invalid_action", "data": data}
        response["action"] = action
        response["correlation_id"] = data.get("correlation_id") or str(uuid.uuid4())
        response["rationale"] = data.get("rationale")
        return response

    def close(self) -> None:
        if not self.alive:
            return
        try:
            self._request({"op": "close"})
        except Exception:
            pass
        kill_process_group(self.process)
        self.alive = False

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        message = {**payload, "protocol": 1, "request_id": request_id}
        self.sent.append(message)
        if not self.alive or self.process.poll() is not None:
            self.alive = False
            return {"outcome": "controller_crash"}
        assert self.process.stdin is not None and self.process.stdout is not None
        deadline = time.monotonic() + self.timeout_s
        try:
            write_frame(self.process.stdin, message, deadline, 1_048_576)
            raw, _stderr = read_frame(self.process.stdout, self.process.stderr, deadline, 1_048_576)
            data = parse_object(raw)
        except FrameTimeout:
            kill_process_group(self.process)
            self.alive = False
            return {"outcome": "timeout", "synthesized_hold": True}
        except (BrokenPipeError, FrameTooLarge, ProtocolError):
            kill_process_group(self.process)
            self.alive = False
            return {"outcome": "invalid_action"}
        if data.get("protocol") not in (None, 1):
            return {"outcome": "invalid_action", "reason": "protocol"}
        if data.get("request_id") not in (None, request_id):
            return {"outcome": "invalid_action", "reason": "request_id"}
        if message["op"] in {"initialize", "reset", "close"} and data.get("ok") is not True:
            return {"outcome": "handshake_failed", "data": data}
        return {"outcome": "ok", "data": data}


def serve(policy: Callable[[dict[str, Any], dict[str, Any], list], dict[str, Any]]) -> None:
    """Точка входа внешнего скрипта: JSON-строки на stdin/stdout."""
    public: dict[str, Any] = {}
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        operation = message.get("op")
        if operation == "initialize":
            public = message.get("public_task_config") or {}
            _write({"ok": True})
        elif operation == "reset":
            _write({"ok": True})
        elif operation == "act":
            action = policy(message.get("observation") or {}, public, message.get("permitted_history") or [])
            _write(_normalize(action))
        elif operation == "close":
            _write({"ok": True})
            return
        else:
            _write({"ok": False, "error": "unknown_op"})


def _normalize(action: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in action.items() if key != "rationale"}
    return {
        "action": body,
        "correlation_id": action.get("correlation_id") or str(uuid.uuid4()),
        "rationale": action.get("rationale", ""),
    }


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
