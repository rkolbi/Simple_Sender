#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Optional (not required by the license): If you make improvements, please consider
# contributing them back upstream (e.g., via a pull request) so others can benefit.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

from simple_sender.utils.atomic_files import atomic_write_json

RUNTIME_MARKER_FILENAME = ".simple_sender_runtime.json"
_WINDOWS_EPOCH_AS_FILETIME_100NS = 116444736000000000


class DuplicateInstanceError(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        title, message = format_duplicate_instance_message(payload)
        self.title = title
        super().__init__(message)


def runtime_marker_path(root_dir: str | Path) -> Path:
    return Path(root_dir) / RUNTIME_MARKER_FILENAME


def read_runtime_marker(root_dir: str | Path) -> dict[str, Any] | None:
    path = runtime_marker_path(root_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _supports_proc_identity_checks() -> bool:
    return os.name == "posix"


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_process_creation_time_100ns(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = getattr(ctypes, "windll", None)
    if kernel32 is None:
        return None
    handle = kernel32.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(pid),
    )
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = kernel32.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    finally:
        try:
            kernel32.kernel32.CloseHandle(handle)
        except Exception:
            pass


def _process_creation_epoch_from_100ns(value: int | None) -> float | None:
    if value is None:
        return None
    return (float(value) - float(_WINDOWS_EPOCH_AS_FILETIME_100NS)) / 10000000.0


def _current_boot_id() -> str | None:
    if not _supports_proc_identity_checks():
        return None
    path = Path("/proc/sys/kernel/random/boot_id")
    try:
        boot_id = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return boot_id or None


def _read_process_cmdline(pid: int) -> list[str] | None:
    if not _supports_proc_identity_checks():
        return None
    path = Path(f"/proc/{int(pid)}/cmdline")
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return [segment.decode("utf-8", errors="replace") for segment in raw.split(b"\x00") if segment]


def _normalized_cmd_arg(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def _cmdline_matches_marker(payload: dict[str, Any], pid: int) -> bool | None:
    process_argv = _read_process_cmdline(pid)
    if not process_argv:
        return None

    marker_argv = payload.get("argv")
    if not isinstance(marker_argv, list) or not marker_argv:
        return None

    marker_first = _normalized_cmd_arg(str(marker_argv[0]))
    if not marker_first:
        return None

    process_norm = [_normalized_cmd_arg(arg) for arg in process_argv]
    if marker_first in process_norm:
        return True

    marker_base = os.path.basename(marker_first)
    if marker_base and any(os.path.basename(arg) == marker_base for arg in process_norm):
        return True

    return False


def _marker_matches_live_process(payload: dict[str, Any], pid: int) -> bool:
    marker_boot_id = str(payload.get("boot_id") or "").strip()
    current_boot_id = _current_boot_id()
    if marker_boot_id and current_boot_id and marker_boot_id != current_boot_id:
        return False

    live_created_100ns = _read_process_creation_time_100ns(pid)
    marker_created_100ns = _coerce_int(payload.get("process_created_100ns"))
    if live_created_100ns is not None:
        if marker_created_100ns is not None:
            if marker_created_100ns != live_created_100ns:
                return False
        else:
            marker_started_epoch = payload.get("started_at_epoch")
            try:
                marker_started_epoch_value = float(marker_started_epoch)
            except (TypeError, ValueError):
                marker_started_epoch_value = None
            live_created_epoch = _process_creation_epoch_from_100ns(live_created_100ns)
            if (
                marker_started_epoch_value is None
                or live_created_epoch is None
                or abs(marker_started_epoch_value - live_created_epoch) > 30.0
            ):
                return False

    cmdline_match = _cmdline_matches_marker(payload, pid)
    if cmdline_match is False:
        return False
    if cmdline_match is None and _supports_proc_identity_checks() and current_boot_id and not marker_boot_id:
        return False

    return True


def _pid_is_running(pid: int) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def active_runtime_marker(
    root_dir: str | Path,
    *,
    current_pid: int | None = None,
    hostname: str | None = None,
) -> dict[str, Any] | None:
    payload = read_runtime_marker(root_dir)
    if not isinstance(payload, dict):
        return None

    try:
        marker_pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return None

    if current_pid is not None and marker_pid == int(current_pid):
        return None

    current_host = str(hostname or socket.gethostname()).strip()
    marker_host = str(payload.get("hostname", "") or "").strip()
    if marker_host and current_host and marker_host != current_host:
        return None

    if _pid_is_running(marker_pid):
        if _marker_matches_live_process(payload, marker_pid):
            return payload
        clear_runtime_marker(root_dir, expected_pid=marker_pid)
        return None

    clear_runtime_marker(root_dir, expected_pid=marker_pid)
    return None


def write_runtime_marker(
    root_dir: str | Path,
    *,
    version: str,
    argv: list[str] | tuple[str, ...] | None = None,
    pid: int | None = None,
    hostname: str | None = None,
    started_at: float | None = None,
) -> Path:
    path = runtime_marker_path(root_dir)
    current_pid = int(pid if pid is not None else os.getpid())
    process_created_100ns = _read_process_creation_time_100ns(current_pid)
    payload = {
        "app": "Simple Sender",
        "version": str(version or "").strip(),
        "pid": current_pid,
        "hostname": str(hostname or socket.gethostname()).strip(),
        "started_at_epoch": float(started_at if started_at is not None else time.time()),
        "argv": [str(item) for item in (argv if argv is not None else sys.argv)],
        "boot_id": _current_boot_id(),
    }
    if process_created_100ns is not None:
        payload["process_created_100ns"] = process_created_100ns
    atomic_write_json(path, payload, indent=2, ensure_ascii=True)
    return path


def format_duplicate_instance_message(payload: dict[str, Any]) -> tuple[str, str]:
    pid = str(payload.get("pid") or "unknown").strip()
    host = str(payload.get("hostname") or "this machine").strip()
    message = (
        "Another Simple Sender instance is already running.\n\n"
        f"Host: {host}\n"
        f"PID: {pid}\n\n"
        "Close the other instance before starting another copy."
    )
    return "Simple Sender Already Running", message


def claim_runtime_marker(
    root_dir: str | Path,
    *,
    version: str,
    argv: list[str] | tuple[str, ...] | None = None,
    pid: int | None = None,
    hostname: str | None = None,
    started_at: float | None = None,
) -> dict[str, Any] | None:
    pid_int = int(pid if pid is not None else os.getpid())
    host = str(hostname or socket.gethostname()).strip()
    active = active_runtime_marker(root_dir, current_pid=pid_int, hostname=host)
    if active is not None:
        return active
    write_runtime_marker(
        root_dir,
        version=version,
        argv=argv,
        pid=pid_int,
        hostname=host,
        started_at=started_at,
    )
    return None


def ensure_runtime_marker_claimed(
    root_dir: str | Path,
    *,
    version: str,
    argv: list[str] | tuple[str, ...] | None = None,
    pid: int | None = None,
    hostname: str | None = None,
    started_at: float | None = None,
) -> None:
    active = claim_runtime_marker(
        root_dir,
        version=version,
        argv=argv,
        pid=pid,
        hostname=hostname,
        started_at=started_at,
    )
    if active is not None:
        raise DuplicateInstanceError(active)


def clear_runtime_marker(root_dir: str | Path, *, expected_pid: int | None = None) -> bool:
    path = runtime_marker_path(root_dir)
    if expected_pid is not None:
        payload = read_runtime_marker(root_dir)
        marker_pid = payload.get("pid") if isinstance(payload, dict) else None
        if marker_pid not in (None, int(expected_pid)):
            return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
