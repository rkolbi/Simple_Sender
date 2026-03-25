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

import logging
from tkinter import messagebox

from simple_sender.ui.ui_actions import request_unit_mode_change

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def load_machine_profiles(app) -> list[dict]:
    raw = app.settings.get("machine_profiles", [])
    profiles: list[dict] = []
    if not isinstance(raw, list):
        return profiles
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        units = str(item.get("units", "mm")).lower()
        units = "inch" if units.startswith("in") else "mm"
        rates = item.get("max_rates", {})
        if not isinstance(rates, dict):
            rates = {}

        def to_float(value):
            try:
                return float(value)
            except Exception:
                return None

        rx = to_float(rates.get("x"))
        ry = to_float(rates.get("y"))
        rz = to_float(rates.get("z"))
        profiles.append(
            {
                "name": name,
                "units": units,
                "max_rates": {"x": rx, "y": ry, "z": rz},
            }
        )
    return profiles


def _saved_active_profile(app) -> str:
    settings = getattr(app, "settings", {})
    if not isinstance(settings, dict):
        return ""
    return str(settings.get("active_profile", "") or "").strip()


def get_profile_by_name(app, name: str):
    if not name:
        return None
    name = str(name).strip()
    for profile in app._machine_profiles:
        if profile.get("name") == name:
            return profile
    return None


def profile_units_scale(units: str) -> float:
    return 25.4 if str(units).lower().startswith("in") else 1.0


def get_profile_rapid_rates(app):
    profile = get_profile_by_name(app, app.active_profile_name.get())
    if not profile:
        return None
    rates = profile.get("max_rates", {})
    try:
        rx = float(rates.get("x"))
        ry = float(rates.get("y"))
        rz = float(rates.get("z"))
    except Exception:
        return None
    if rx <= 0 or ry <= 0 or rz <= 0:
        return None
    scale = profile_units_scale(profile.get("units", "mm"))
    return (rx * scale, ry * scale, rz * scale)


def refresh_profile_combo(app):
    names = [p.get("name", "") for p in app._machine_profiles]
    if hasattr(app, "profile_combo"):
        app.profile_combo["values"] = names
    current = app.active_profile_name.get()
    if current not in names:
        saved = _saved_active_profile(app)
        if saved in names:
            app.active_profile_name.set(saved)
        elif names:
            app.active_profile_name.set(names[0])
        else:
            app.active_profile_name.set("")


def apply_profile_to_vars(app, profile: dict | None):
    if not profile:
        app.profile_name_var.set("")
        app.profile_units_var.set("mm")
        app.profile_rate_x_var.set("")
        app.profile_rate_y_var.set("")
        app.profile_rate_z_var.set("")
        if hasattr(app, "profile_rate_units"):
            app.profile_rate_units.config(text="mm/min")
        return
    app.profile_name_var.set(profile.get("name", ""))
    units = profile.get("units", "mm")
    app.profile_units_var.set(units)
    rates = profile.get("max_rates", {})
    app.profile_rate_x_var.set("" if rates.get("x") is None else str(rates.get("x")))
    app.profile_rate_y_var.set("" if rates.get("y") is None else str(rates.get("y")))
    app.profile_rate_z_var.set("" if rates.get("z") is None else str(rates.get("z")))
    update_profile_units_label(app)


def update_profile_units_label(app):
    units = str(app.profile_units_var.get()).lower()
    label = "in/min" if units.startswith("in") else "mm/min"
    if hasattr(app, "profile_rate_units"):
        try:
            app.profile_rate_units.config(text=label)
        except Exception as exc:
            _log_suppressed("Failed updating machine-profile rate units label", exc)


def on_profile_units_change(app, _event=None):
    update_profile_units_label(app)


def apply_profile_units(app, profile: dict | None):
    if not profile:
        return True
    units = profile.get("units", "mm")
    if units not in ("mm", "inch"):
        units = "mm"
    return bool(request_unit_mode_change(app, str(units), source="profile"))


def _persist_machine_profiles(app) -> bool:
    try:
        if not isinstance(getattr(app, "settings", None), dict):
            app.settings = {}
        app.settings["machine_profiles"] = list(app._machine_profiles)
        app.settings["active_profile"] = str(app.active_profile_name.get() or "").strip()
    except Exception as exc:
        _log_suppressed("Failed syncing machine profiles into settings state", exc)
        return False
    saver = getattr(app, "_save_settings", None)
    if callable(saver):
        try:
            saver()
        except Exception as exc:
            _log_suppressed("Failed persisting machine profiles to disk", exc)
            return False
    return True


def _persist_active_profile_selection(app) -> bool:
    try:
        if not isinstance(getattr(app, "settings", None), dict):
            app.settings = {}
        app.settings["active_profile"] = str(app.active_profile_name.get() or "").strip()
    except Exception as exc:
        _log_suppressed("Failed syncing active machine profile into settings state", exc)
        return False
    saver = getattr(app, "_save_settings", None)
    if callable(saver):
        try:
            saver()
        except Exception as exc:
            _log_suppressed("Failed persisting active machine profile selection", exc)
            return False
    return True


def _status_text(app) -> str:
    try:
        return str(app.status.cget("text") or "")
    except Exception:
        return ""


def _report_profile_persistence_failure(app, action: str, name: str) -> None:
    message = (
        f"Profile {action} in memory only: settings could not be saved. "
        f"The change for '{name}' will not survive restart."
    )
    try:
        messagebox.showwarning("Profile", message)
    except Exception as exc:
        _log_suppressed("Failed showing machine-profile persistence warning", exc)
    try:
        app.status.config(text=message)
    except Exception as exc:
        _log_suppressed("Failed updating status text for machine-profile persistence warning", exc)


def _report_profile_status(
    app,
    *,
    action: str,
    name: str,
    persisted: bool,
    unit_change_ok: bool,
) -> None:
    if not persisted:
        _report_profile_persistence_failure(app, action, name)
        return
    if unit_change_ok:
        try:
            app.status.config(text=f"Profile {action}: {name}")
        except Exception as exc:
            _log_suppressed("Failed updating machine-profile success status", exc)
        return
    current_status = _status_text(app).strip()
    if current_status:
        combined = f"Profile {action}: {name}. {current_status}"
    else:
        combined = f"Profile {action}: {name}, but unit change confirmation is still pending or failed."
    try:
        app.status.config(text=combined)
    except Exception as exc:
        _log_suppressed("Failed updating machine-profile mixed-result status", exc)


def on_profile_select(app, _event=None):
    name = app.active_profile_name.get()
    profile = get_profile_by_name(app, name)
    if not profile:
        return
    persisted = _persist_active_profile_selection(app)
    apply_profile_to_vars(app, profile)
    apply_profile_units(app, profile)
    if app._last_gcode_lines:
        app._update_gcode_stats(app._last_gcode_lines)
    if not persisted:
        _report_profile_persistence_failure(app, "selected", name)


def new_profile(app):
    try:
        app.profile_combo.set("")
    except Exception as exc:
        _log_suppressed("Failed clearing profile combobox selection in new_profile", exc)
    app.active_profile_name.set("")
    app.profile_name_var.set("")
    app.profile_units_var.set(app.unit_mode.get())
    rates = None
    if app._rapid_rates:
        scale = profile_units_scale(app.unit_mode.get())
        rates = (
            app._rapid_rates[0] / scale,
            app._rapid_rates[1] / scale,
            app._rapid_rates[2] / scale,
        )
    if rates:
        app.profile_rate_x_var.set(f"{rates[0]:.3f}")
        app.profile_rate_y_var.set(f"{rates[1]:.3f}")
        app.profile_rate_z_var.set(f"{rates[2]:.3f}")
    else:
        app.profile_rate_x_var.set("")
        app.profile_rate_y_var.set("")
        app.profile_rate_z_var.set("")
    update_profile_units_label(app)


def save_profile(app):
    name = app.profile_name_var.get().strip()
    if not name:
        messagebox.showwarning("Profile", "Enter a profile name.")
        return
    units = str(app.profile_units_var.get()).lower()
    units = "inch" if units.startswith("in") else "mm"

    def parse_rate(var, label):
        raw = var.get().strip()
        if not raw:
            raise ValueError(f"Missing {label} rate.")
        value = float(raw)
        if value <= 0:
            raise ValueError(f"{label} rate must be positive.")
        return value

    try:
        rx = parse_rate(app.profile_rate_x_var, "X")
        ry = parse_rate(app.profile_rate_y_var, "Y")
        rz = parse_rate(app.profile_rate_z_var, "Z")
    except Exception as exc:
        messagebox.showwarning("Profile", str(exc))
        return

    profile = {"name": name, "units": units, "max_rates": {"x": rx, "y": ry, "z": rz}}
    found = False
    for i, existing in enumerate(app._machine_profiles):
        if existing.get("name") == name:
            app._machine_profiles[i] = profile
            found = True
            break
    if not found:
        app._machine_profiles.append(profile)
    app.active_profile_name.set(name)
    persisted = _persist_machine_profiles(app)
    refresh_profile_combo(app)
    try:
        app.profile_combo.set(name)
    except Exception as exc:
        _log_suppressed("Failed selecting saved machine profile in combobox", exc)
    apply_profile_to_vars(app, profile)
    unit_change_ok = apply_profile_units(app, profile)
    if app._last_gcode_lines:
        app._update_gcode_stats(app._last_gcode_lines)
    _report_profile_status(
        app,
        action="saved",
        name=name,
        persisted=bool(persisted),
        unit_change_ok=bool(unit_change_ok),
    )


def delete_profile(app):
    name = app.active_profile_name.get().strip()
    if not name:
        messagebox.showwarning("Profile", "Select a profile to delete.")
        return
    if not messagebox.askyesno("Profile", f"Delete profile '{name}'?"):
        return
    app._machine_profiles = [p for p in app._machine_profiles if p.get("name") != name]
    refresh_profile_combo(app)
    persisted = _persist_machine_profiles(app)
    profile = get_profile_by_name(app, app.active_profile_name.get())
    apply_profile_to_vars(app, profile)
    unit_change_ok = True
    if profile:
        unit_change_ok = apply_profile_units(app, profile)
    if app._last_gcode_lines:
        app._update_gcode_stats(app._last_gcode_lines)
    _report_profile_status(
        app,
        action="deleted",
        name=name,
        persisted=bool(persisted),
        unit_change_ok=bool(unit_change_ok),
    )
