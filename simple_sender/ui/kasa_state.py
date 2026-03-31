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

from typing import Any, cast


OUTLET_LABELS = ("Outlet 1", "Outlet 2")


def kasa_supported(platform: str) -> bool:
    return str(platform or "").startswith("linux")


def outlet_label(outlet_id: int) -> str:
    return f"Outlet {2 if int(outlet_id) == 2 else 1}"


def outlet_id_from_label(label: str, default: int) -> int:
    text = str(label or "").strip().lower()
    if text.endswith("2"):
        return 2
    if text.endswith("1"):
        return 1
    return 2 if int(default) == 2 else 1


def read_var_value(app, attr_name: str, default: Any) -> Any:
    var = getattr(app, attr_name, None)
    if var is None:
        return default
    getter = getattr(var, "get", None)
    if not callable(getter):
        return default
    try:
        return getter()
    except Exception:
        return default


def _normalized_outlet_id(value: object, default: int) -> int:
    try:
        outlet_id = int(value)
    except Exception:
        outlet_id = default
    return 1 if outlet_id == 1 else 2


def _normalized_outlet_count(value: object, default: int = 2) -> int:
    try:
        outlet_count = int(value)
    except Exception:
        outlet_count = default
    return max(1, outlet_count)


def _read_delay_seconds(value: object) -> float:
    try:
        delay = float(value)
    except Exception:
        delay = 0.0
    return max(0.0, delay)


def kasa_settings_snapshot(app, *, platform: str) -> dict[str, Any]:
    if not kasa_supported(platform):
        return {
            "kasa_enabled": False,
            "kasa_device_identifier": "",
            "kasa_outlet_count": 2,
            "vacuum_enabled": False,
            "vacuum_off_delay_sec": 0.0,
            "vacuum_outlet": 1,
            "light_enabled": False,
            "light_outlet": 2,
        }
    return {
        "kasa_enabled": bool(read_var_value(app, "kasa_enabled", False)),
        "kasa_device_identifier": str(
            read_var_value(app, "kasa_device_identifier", "") or ""
        ).strip(),
        "kasa_outlet_count": _normalized_outlet_count(
            getattr(app, "_kasa_outlet_count", 2),
        ),
        "vacuum_enabled": bool(read_var_value(app, "vacuum_enabled", False)),
        "vacuum_off_delay_sec": _read_delay_seconds(
            read_var_value(app, "vacuum_off_delay_sec", 0.0)
        ),
        "vacuum_outlet": _normalized_outlet_id(
            read_var_value(app, "vacuum_outlet", 1),
            1,
        ),
        "light_enabled": bool(read_var_value(app, "light_enabled", False)),
        "light_outlet": _normalized_outlet_id(
            read_var_value(app, "light_outlet", 2),
            2,
        ),
    }


def kasa_status_snapshot(app, *, platform: str) -> dict[str, Any]:
    settings = kasa_settings_snapshot(app, platform=platform)
    outlet_count = _normalized_outlet_count(settings.get("kasa_outlet_count", 2))
    vacuum_enabled = bool(settings.get("vacuum_enabled", False))
    light_enabled = bool(settings.get("light_enabled", False)) and outlet_count >= 2
    return {
        "enabled": bool(settings.get("kasa_enabled", False)),
        "device_identifier": str(settings.get("kasa_device_identifier", "") or "").strip(),
        "outlet_count": outlet_count,
        "vacuum_enabled": vacuum_enabled,
        "vacuum_outlet": _normalized_outlet_id(settings.get("vacuum_outlet", 1), 1),
        "vacuum_on": (
            bool(getattr(app, "_kasa_vacuum_quick_on", False)) if vacuum_enabled else None
        ),
        "light_enabled": light_enabled,
        "light_outlet": _normalized_outlet_id(settings.get("light_outlet", 2), 2),
        "light_on": (
            bool(getattr(app, "_kasa_light_quick_on", False)) if light_enabled else None
        ),
    }


def _format_channel_status(
    name: str,
    *,
    enabled: bool,
    outlet: object,
    state: bool | None,
) -> str:
    if not enabled:
        return f"{name}=disabled"
    state_text = "ON" if bool(state) else "OFF"
    return f"{name}=Outlet {int(_normalized_outlet_id(outlet, 1))}:{state_text}"


def format_kasa_status_line(app, *, platform: str) -> str:
    if not kasa_supported(platform):
        return "enabled=False | linux_only=True"
    snapshot = kasa_status_snapshot(app, platform=platform)
    enabled = bool(snapshot.get("enabled", False))
    device = str(snapshot.get("device_identifier", "") or "").strip() or "none"
    vacuum_text = _format_channel_status(
        "vacuum",
        enabled=bool(snapshot.get("vacuum_enabled", False)),
        outlet=snapshot.get("vacuum_outlet", 1),
        state=cast(bool | None, snapshot.get("vacuum_on")),
    )
    light_text = _format_channel_status(
        "light",
        enabled=bool(snapshot.get("light_enabled", False)),
        outlet=snapshot.get("light_outlet", 2),
        state=cast(bool | None, snapshot.get("light_on")),
    )
    return (
        f"enabled={enabled} | device={device} | outlets={int(snapshot.get('outlet_count', 2) or 2)}"
        f" | {vacuum_text} | {light_text}"
    )
