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

from tkinter import messagebox


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
        if names:
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
        except Exception:
            pass


def on_profile_units_change(app, _event=None):
    update_profile_units_label(app)


def apply_profile_units(app, profile: dict | None):
    if not profile:
        return
    units = profile.get("units", "mm")
    if units not in ("mm", "inch"):
        units = "mm"
    app._set_unit_mode(units)


def on_profile_select(app, _event=None):
    name = app.active_profile_name.get()
    profile = get_profile_by_name(app, name)
    if not profile:
        return
    apply_profile_to_vars(app, profile)
    apply_profile_units(app, profile)
    if app._last_gcode_lines:
        app._update_gcode_stats(app._last_gcode_lines)


def new_profile(app):
    try:
        app.profile_combo.set("")
    except Exception:
        pass
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
    refresh_profile_combo(app)
    try:
        app.profile_combo.set(name)
    except Exception:
        pass
    apply_profile_to_vars(app, profile)
    apply_profile_units(app, profile)
    if app._last_gcode_lines:
        app._update_gcode_stats(app._last_gcode_lines)
    app.status.config(text=f"Profile saved: {name}")


def delete_profile(app):
    name = app.active_profile_name.get().strip()
    if not name:
        messagebox.showwarning("Profile", "Select a profile to delete.")
        return
    if not messagebox.askyesno("Profile", f"Delete profile '{name}'?"):
        return
    app._machine_profiles = [p for p in app._machine_profiles if p.get("name") != name]
    refresh_profile_combo(app)
    profile = get_profile_by_name(app, app.active_profile_name.get())
    apply_profile_to_vars(app, profile)
    if profile:
        apply_profile_units(app, profile)
    if app._last_gcode_lines:
        app._update_gcode_stats(app._last_gcode_lines)
    app.status.config(text=f"Profile deleted: {name}")
