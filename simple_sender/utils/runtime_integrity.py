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

import errno
import hashlib
import json
import os
import socket
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from simple_sender.utils.atomic_files import atomic_write_json

RUNTIME_MARKER_FILENAME = ".simple_sender_runtime.json"
RUNTIME_LOCK_FILENAME = ".simple_sender_runtime.lock"
_WINDOWS_EPOCH_AS_FILETIME_100NS = 116444736000000000
_RUNTIME_APP_DIR = "simple_sender"
_RUNTIME_INSTANCE_DIR = "instances"
_RUNTIME_LOCKS: dict[str, dict[str, Any]] = {}


class DuplicateInstanceError(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        title, message = format_duplicate_instance_message(payload)
        self.title = title
        super().__init__(message)


def _ensure_writable_dir(path: str | Path) -> bool:
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return bool(os.path.isdir(path) and os.access(path, os.W_OK))


def _normalized_root_dir(root_dir: str | Path) -> str:
    root_text = str(root_dir or "").strip()
    if not root_text:
        root_text = os.getcwd()
    return os.path.normcase(os.path.normpath(os.path.abspath(root_text)))


def _runtime_scope_dir_name(root_dir: str | Path) -> str:
    normalized = _normalized_root_dir(root_dir)
    digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]
    base = os.path.basename(normalized).strip() or "root"
    safe_base = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in base).strip("-_")
    safe_base = safe_base or "root"
    return f"{safe_base}-{digest}"


def _runtime_state_base_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.name == "nt":
        local_appdata = str(os.environ.get("LOCALAPPDATA", "") or "").strip()
        appdata = str(os.environ.get("APPDATA", "") or "").strip()
        if local_appdata:
            candidates.append(Path(local_appdata) / _RUNTIME_APP_DIR / "state")
        if appdata:
            candidates.append(Path(appdata) / _RUNTIME_APP_DIR / "state")
        candidates.append(Path.home() / "AppData" / "Local" / _RUNTIME_APP_DIR / "state")
    else:
        xdg_state_home = str(os.environ.get("XDG_STATE_HOME", "") or "").strip()
        xdg_config_home = str(os.environ.get("XDG_CONFIG_HOME", "") or "").strip()
        if xdg_state_home:
            candidates.append(Path(xdg_state_home) / _RUNTIME_APP_DIR)
        candidates.append(Path.home() / ".local" / "state" / _RUNTIME_APP_DIR)
        if xdg_config_home:
            candidates.append(Path(xdg_config_home) / _RUNTIME_APP_DIR / "state")
        candidates.append(Path.home() / ".config" / _RUNTIME_APP_DIR / "state")
    candidates.append(Path(tempfile.gettempdir()) / _RUNTIME_APP_DIR / "state")
    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(str(candidate)))
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return unique_candidates


@lru_cache(maxsize=32)
def get_runtime_state_dir(root_dir: str | Path) -> Path:
    scope_dir = _runtime_scope_dir_name(root_dir)
    for base_dir in _runtime_state_base_candidates():
        candidate = base_dir / _RUNTIME_INSTANCE_DIR / scope_dir
        if _ensure_writable_dir(candidate):
            return candidate
    fallback = Path(tempfile.gettempdir()) / _RUNTIME_APP_DIR / "state" / _RUNTIME_INSTANCE_DIR / scope_dir
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def runtime_lock_path(root_dir: str | Path) -> Path:
    return get_runtime_state_dir(root_dir) / RUNTIME_LOCK_FILENAME


def _runtime_lock_key(root_dir: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(runtime_lock_path(root_dir))))


def _lock_file_nonblocking(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    fcntl = cast(Any, __import__("fcntl"))

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    fcntl = cast(Any, __import__("fcntl"))

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _claim_runtime_lock(root_dir: str | Path, *, pid: int) -> bool:
    pid_int = int(pid)
    lock_key = _runtime_lock_key(root_dir)
    existing = _RUNTIME_LOCKS.get(lock_key)
    if existing is not None and int(existing.get("pid", -1)) == pid_int:
        return True

    lock_path = runtime_lock_path(root_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        _lock_file_nonblocking(handle)
    except OSError as exc:
        handle.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN, 13):
            return False
        raise
    _RUNTIME_LOCKS[lock_key] = {"pid": pid_int, "handle": handle}
    return True


def _release_runtime_lock(root_dir: str | Path, *, expected_pid: int | None = None) -> bool:
    lock_key = _runtime_lock_key(root_dir)
    existing = _RUNTIME_LOCKS.get(lock_key)
    if existing is None:
        return False
    lock_pid = int(existing.get("pid", -1))
    if expected_pid is not None and lock_pid != int(expected_pid):
        return False
    handle = existing.get("handle")
    try:
        if handle is not None:
            _unlock_file(handle)
    finally:
        try:
            if handle is not None:
                handle.close()
        finally:
            _RUNTIME_LOCKS.pop(lock_key, None)
    return True


def _clear_runtime_locks_for_tests() -> None:
    for lock_key, existing in list(_RUNTIME_LOCKS.items()):
        handle = existing.get("handle")
        try:
            if handle is not None:
                _unlock_file(handle)
        except Exception:
            pass
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass
        _RUNTIME_LOCKS.pop(lock_key, None)


def _read_runtime_marker_with_retry(
    root_dir: str | Path,
    *,
    attempts: int = 25,
    delay_s: float = 0.01,
) -> dict[str, Any] | None:
    for attempt in range(max(1, int(attempts))):
        payload = read_runtime_marker(root_dir)
        if payload is not None:
            return payload
        if attempt + 1 < max(1, int(attempts)):
            time.sleep(max(0.0, float(delay_s)))
    return None


def _build_runtime_marker_payload(
    *,
    version: str,
    argv: list[str] | tuple[str, ...] | None = None,
    pid: int | None = None,
    hostname: str | None = None,
    started_at: float | None = None,
) -> dict[str, Any]:
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
    return payload


def runtime_marker_path(root_dir: str | Path) -> Path:
    return get_runtime_state_dir(root_dir) / RUNTIME_MARKER_FILENAME


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


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
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
            marker_started_epoch_value = _coerce_float(payload.get("started_at_epoch"))
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

    marker_pid = _coerce_int(payload.get("pid"))
    if marker_pid is None:
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
    payload = _build_runtime_marker_payload(
        version=version,
        argv=argv,
        pid=pid,
        hostname=hostname,
        started_at=started_at,
    )
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
    claimed = _claim_runtime_lock(root_dir, pid=pid_int)
    if not claimed:
        active = _read_runtime_marker_with_retry(root_dir)
        if active is not None:
            return active
        return {
            "app": "Simple Sender",
            "version": str(version or "").strip(),
            "pid": "unknown",
            "hostname": host or "unknown",
        }
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
    removed = False
    try:
        path.unlink()
        removed = True
    except FileNotFoundError:
        removed = False
    except OSError:
        removed = False
    lock_released = _release_runtime_lock(root_dir, expected_pid=expected_pid)
    return bool(removed or lock_released)
