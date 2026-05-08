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

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


_SYSTEM_INFO_COMMAND_TIMEOUT_S = 2.0
_SYSTEM_INFO_COMMAND_MAX_CHARS = 16_000


def json_dump(obj: Any, *, log_suppressed: Callable[[str, BaseException], None]) -> str:
    def _default(value: Any):
        return str(value)

    try:
        return json.dumps(obj, indent=2, sort_keys=True, default=_default)
    except Exception as exc:
        log_suppressed("Failed serializing diagnostics JSON payload", exc)
        return "{}"


def collect_build_info(
    app: Any,
    *,
    diagnostics_schema_rev: str,
    package_version: str,
    log_suppressed: Callable[[str, BaseException], None],
) -> dict[str, Any]:
    version_getter = getattr(getattr(app, "version_var", None), "get", None)
    app_version = str(version_getter() if callable(version_getter) else "").strip()
    info: dict[str, Any] = {
        "schema_rev": diagnostics_schema_rev,
        "package_version": str(package_version or "").strip(),
        "app_version": app_version,
        "build_commit": "",
        "build_source": "",
    }
    env_commit = str(os.environ.get("SIMPLE_SENDER_BUILD_COMMIT", "") or "").strip()
    if env_commit:
        info["build_commit"] = env_commit
        info["build_source"] = "env:SIMPLE_SENDER_BUILD_COMMIT"
        return info
    git_exe = str(shutil.which("git") or "").strip()
    if not git_exe:
        return info
    cwd = ""
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    if not cwd:
        return info
    try:
        proc = subprocess.run(
            [git_exe, "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.5,
            check=False,
        )
        commit = str(proc.stdout or "").strip()
        if proc.returncode == 0 and commit:
            info["build_commit"] = commit
            info["build_source"] = "git"
    except Exception as exc:
        log_suppressed("Failed collecting git commit metadata for diagnostics", exc)
    return info


def _run_system_snapshot_command(
    args: list[str],
    *,
    timeout_s: float = _SYSTEM_INFO_COMMAND_TIMEOUT_S,
    max_chars: int = _SYSTEM_INFO_COMMAND_MAX_CHARS,
) -> str:
    executable = str(args[0] if args else "").strip()
    if not executable:
        return "skipped: empty command"
    if shutil.which(executable) is None:
        return "unavailable"
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=max(0.1, float(timeout_s)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"
    output = str(proc.stdout or "").strip()
    if len(output) > int(max_chars):
        output = f"{output[: max(0, int(max_chars) - 80)]}\n... [truncated]"
    if proc.returncode == 0:
        return output or "ok"
    prefix = f"exit={proc.returncode}"
    return f"{prefix}\n{output}" if output else prefix


def _append_command_snapshot(
    lines: list[str],
    title: str,
    args: list[str],
    *,
    timeout_s: float = _SYSTEM_INFO_COMMAND_TIMEOUT_S,
    max_chars: int = _SYSTEM_INFO_COMMAND_MAX_CHARS,
) -> None:
    lines.append("")
    lines.append(f"--- {title} ---")
    lines.append(f"$ {' '.join(args)}")
    lines.append(
        _run_system_snapshot_command(
            args,
            timeout_s=timeout_s,
            max_chars=max_chars,
        )
    )


def _append_file_snapshot(
    lines: list[str],
    title: str,
    path: Path,
    *,
    max_chars: int = 8_000,
) -> None:
    lines.append("")
    lines.append(f"--- {title} ---")
    lines.append(str(path))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        lines.append(f"error: {type(exc).__name__}: {exc}")
        return
    if len(text) > int(max_chars):
        text = f"{text[: max(0, int(max_chars) - 80)]}\n... [truncated]"
    lines.append(text.strip() or "empty")


def _append_linux_network_snapshot(lines: list[str]) -> None:
    lines.append("")
    lines.append("Linux/Raspberry Pi network diagnostics")
    if platform.system().lower() != "linux":
        lines.append(f"Skipped: platform is {platform.system() or 'unknown'}, not Linux.")
        return

    command_specs: list[tuple[str, list[str], float, int]] = [
        ("hostname", ["hostname"], 1.0, 2_000),
        ("hostname -I", ["hostname", "-I"], 1.0, 2_000),
        ("ip addr", ["ip", "addr"], 2.0, 16_000),
        ("ip route", ["ip", "route"], 2.0, 8_000),
        ("resolvectl status", ["resolvectl", "status"], 2.0, 16_000),
        ("ssh service active", ["systemctl", "is-active", "ssh"], 1.0, 2_000),
        ("ssh service status", ["systemctl", "status", "ssh", "--no-pager"], 2.0, 12_000),
        ("ssh journal tail", ["journalctl", "-u", "ssh", "--no-pager", "-n", "100"], 2.0, 16_000),
        ("kernel journal tail", ["journalctl", "-k", "--no-pager", "-n", "200"], 2.0, 24_000),
        ("dmesg tail", ["dmesg"], 2.0, 24_000),
        ("iw dev", ["iw", "dev"], 1.5, 8_000),
        ("iwconfig", ["iwconfig"], 1.5, 8_000),
        ("rfkill list", ["rfkill", "list"], 1.5, 8_000),
        ("networking status", ["systemctl", "status", "networking", "--no-pager"], 2.0, 12_000),
        ("NetworkManager status", ["systemctl", "status", "NetworkManager", "--no-pager"], 2.0, 12_000),
        ("systemd-networkd status", ["systemctl", "status", "systemd-networkd", "--no-pager"], 2.0, 12_000),
        ("uptime", ["uptime"], 1.0, 2_000),
        ("memory summary", ["free", "-h"], 1.0, 4_000),
        ("load average", ["cat", "/proc/loadavg"], 1.0, 2_000),
        (
            "Simple Sender process state",
            ["ps", "-o", "pid,ppid,stat,nlwp,pcpu,pmem,comm", "-p", str(os.getpid())],
            1.0,
            4_000,
        ),
    ]
    for title, args, timeout_s, max_chars in command_specs:
        if title == "dmesg tail":
            lines.append("")
            lines.append("--- dmesg tail ---")
            lines.append("$ dmesg | tail -n 200")
            output = _run_system_snapshot_command(
                args,
                timeout_s=timeout_s,
                max_chars=64_000,
            )
            dmesg_lines = output.splitlines()
            if len(dmesg_lines) > 200:
                output = "\n".join(dmesg_lines[-200:])
            lines.append(output)
            continue
        _append_command_snapshot(
            lines,
            title,
            args,
            timeout_s=timeout_s,
            max_chars=max_chars,
        )

    if shutil.which("resolvectl") is None:
        _append_file_snapshot(lines, "/etc/resolv.conf", Path("/etc/resolv.conf"))


def build_system_info_text(
    app: Any,
    *,
    collect_build_info: Callable[[Any], dict[str, Any]],
    build_info: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("Simple Sender system snapshot")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    version_getter = getattr(getattr(app, "version_var", None), "get", None)
    version_text = str(version_getter() if callable(version_getter) else "").strip()
    if version_text:
        lines.append(f"App version: {version_text}")
    if build_info is None:
        build_info = collect_build_info(app)
    if isinstance(build_info, dict):
        schema_rev = str(build_info.get("schema_rev", "") or "").strip()
        if schema_rev:
            lines.append(f"Diagnostics schema: {schema_rev}")
        package_version = str(build_info.get("package_version", "") or "").strip()
        if package_version:
            lines.append(f"Package version: {package_version}")
        commit = str(build_info.get("build_commit", "") or "").strip()
        source = str(build_info.get("build_source", "") or "").strip()
        if commit:
            lines.append(f"Build commit: {commit}")
        if source:
            lines.append(f"Build source: {source}")
    lines.append(f"Python: {sys.version.splitlines()[0] if sys.version else 'n/a'}")
    lines.append(f"Executable: {sys.executable}")
    lines.append(f"Platform: {platform.platform()}")
    lines.append(f"Machine: {platform.machine()}")
    lines.append(f"Processor: {platform.processor()}")
    lines.append(f"PID: {os.getpid()}")
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    if cwd:
        lines.append(f"CWD: {cwd}")
    env = os.environ
    for key in ("USER", "LOGNAME", "HOME", "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY"):
        value = str(env.get(key, "") or "").strip()
        if value:
            lines.append(f"{key}: {value}")
    _append_linux_network_snapshot(lines)
    return "\n".join(lines) + "\n"


def resolved_settings_path(app: Any) -> Path | None:
    raw_path = str(getattr(app, "settings_path", "") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    return path


def pi_profile_enabled(app: Any) -> bool:
    var = getattr(app, "pi_profile_enabled", None)
    if var is not None:
        try:
            return bool(var.get())
        except Exception:
            pass
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get("pi_profile_enabled", False))
        except Exception:
            return False
    return False


def effective_line_cache_cap_lines(
    app: Any,
    *,
    default_cap: int,
    low_power_cap: int,
    pi_profile_enabled: Callable[[Any], bool],
) -> tuple[int, str]:
    stored = getattr(app, "_gcode_full_line_cache_cap_lines", None)
    if stored is not None:
        try:
            cap = max(0, int(stored))
            profile = str(
                getattr(app, "_gcode_full_line_cache_profile", "") or ""
            ).strip()
            if profile:
                return cap, profile
        except Exception:
            pass
    if pi_profile_enabled(app):
        return int(low_power_cap), "pi"
    return int(default_cap), "default"


def headless_live_state_line_estimate(gview: Any) -> int:
    if gview is None:
        return 0
    try:
        return int(getattr(gview, "lines_count", 0) or 0)
    except Exception:
        return 0


def bounded_ssmeta(ssmeta: Any) -> dict[str, str]:
    if not isinstance(ssmeta, dict):
        return {}
    out: dict[str, str] = {}
    for raw_key in sorted(ssmeta.keys())[:64]:
        key = str(raw_key or "").strip()
        if not key:
            continue
        value = str(ssmeta.get(raw_key, "") or "").strip()
        if len(value) > 256:
            value = f"{value[:253]}..."
        out[key] = value
    return out
