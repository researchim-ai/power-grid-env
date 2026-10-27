"""Ограниченный обмен одной JSON-строкой с дочерним процессом.

Дедлайн общий на запись и чтение. Первый байт не снимает ограничение:
строка без перевода строки обрывается по времени, а не ждёт newline.
"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import time
from typing import Any, BinaryIO


class FrameTimeout(TimeoutError):
    pass


class FrameTooLarge(ValueError):
    pass


class ProtocolError(ValueError):
    pass


def write_frame(stream: BinaryIO, payload: Any, deadline: float, max_bytes: int) -> None:
    data = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > max_bytes:
        raise FrameTooLarge(f"исходящий кадр {len(data)} байт больше лимита {max_bytes}")
    fd = stream.fileno()
    os.set_blocking(fd, False)
    sent = 0
    try:
        while sent < len(data):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrameTimeout("истёк дедлайн записи")
            _, writable, _ = select.select([], [fd], [], remaining)
            if not writable:
                raise FrameTimeout("истёк дедлайн записи")
            try:
                count = os.write(fd, data[sent:])
            except BlockingIOError:
                continue
            if count <= 0:
                raise BrokenPipeError
            sent += count
    finally:
        os.set_blocking(fd, True)


def read_frame(
    stdout: BinaryIO,
    stderr: BinaryIO | None,
    deadline: float,
    max_bytes: int,
    stderr_limit: int = 65536,
) -> tuple[str, str]:
    out_fd = stdout.fileno()
    err_fd = None if stderr is None else stderr.fileno()
    os.set_blocking(out_fd, False)
    if err_fd is not None:
        os.set_blocking(err_fd, False)
    buffer = bytearray()
    captured = bytearray()
    try:
        while b"\n" not in buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrameTimeout("истёк дедлайн чтения")
            watch = [out_fd] if err_fd is None else [out_fd, err_fd]
            ready, _, _ = select.select(watch, [], [], remaining)
            if not ready:
                raise FrameTimeout("истёк дедлайн чтения")
            if err_fd is not None and err_fd in ready:
                _drain(err_fd, captured, stderr_limit)
            if out_fd in ready:
                try:
                    piece = os.read(out_fd, 65536)
                except BlockingIOError:
                    continue
                if not piece:
                    raise BrokenPipeError
                buffer.extend(piece)
                if len(buffer) > max_bytes:
                    raise FrameTooLarge(f"входящий кадр больше {max_bytes} байт")
    finally:
        os.set_blocking(out_fd, True)
        if err_fd is not None:
            os.set_blocking(err_fd, True)
    line = bytes(buffer).split(b"\n", 1)[0]
    return line.decode("utf-8"), captured.decode("utf-8", errors="replace")


def parse_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("json") from exc
    if not isinstance(data, dict):
        raise ProtocolError("json_root")
    return data


def kill_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=2)
        except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
            return


def _drain(fd: int, captured: bytearray, limit: int) -> None:
    while True:
        try:
            piece = os.read(fd, 65536)
        except BlockingIOError:
            return
        if not piece:
            return
        if len(captured) < limit:
            captured.extend(piece[: limit - len(captured)])
