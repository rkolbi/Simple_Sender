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

from dataclasses import dataclass


@dataclass(slots=True)
class DiagnosticsExportRuntimeState:
    diagnostics_bundle_async_export: bool = True
    diagnostics_bundle_export_inflight: bool = False
    diagnostics_report_async_export: bool = True
    diagnostics_report_export_inflight: bool = False
    backup_bundle_async_io: bool = True
    backup_bundle_export_inflight: bool = False
    backup_bundle_import_inflight: bool = False


def _safe_bool_attr(app: object, name: str, fallback: bool = False) -> bool:
    try:
        return bool(getattr(app, name, fallback))
    except Exception:
        return bool(fallback)


def sync_diagnostics_export_runtime_state_from_app(
    app: object,
    state: DiagnosticsExportRuntimeState,
) -> DiagnosticsExportRuntimeState:
    state.diagnostics_bundle_async_export = _safe_bool_attr(
        app,
        "_diagnostics_bundle_async_export",
        state.diagnostics_bundle_async_export,
    )
    state.diagnostics_bundle_export_inflight = _safe_bool_attr(
        app,
        "_diagnostics_bundle_export_inflight",
        state.diagnostics_bundle_export_inflight,
    )
    state.diagnostics_report_async_export = _safe_bool_attr(
        app,
        "_diagnostics_report_async_export",
        state.diagnostics_report_async_export,
    )
    state.diagnostics_report_export_inflight = _safe_bool_attr(
        app,
        "_diagnostics_report_export_inflight",
        state.diagnostics_report_export_inflight,
    )
    state.backup_bundle_async_io = _safe_bool_attr(
        app,
        "_backup_bundle_async_io",
        state.backup_bundle_async_io,
    )
    state.backup_bundle_export_inflight = _safe_bool_attr(
        app,
        "_backup_bundle_export_inflight",
        state.backup_bundle_export_inflight,
    )
    state.backup_bundle_import_inflight = _safe_bool_attr(
        app,
        "_backup_bundle_import_inflight",
        state.backup_bundle_import_inflight,
    )
    return state


def sync_diagnostics_export_runtime_state_to_app(
    app: object,
    state: DiagnosticsExportRuntimeState,
) -> DiagnosticsExportRuntimeState:
    setattr(app, "_diagnostics_export_runtime_state", state)
    setattr(
        app,
        "_diagnostics_bundle_async_export",
        bool(state.diagnostics_bundle_async_export),
    )
    setattr(
        app,
        "_diagnostics_bundle_export_inflight",
        bool(state.diagnostics_bundle_export_inflight),
    )
    setattr(
        app,
        "_diagnostics_report_async_export",
        bool(state.diagnostics_report_async_export),
    )
    setattr(
        app,
        "_diagnostics_report_export_inflight",
        bool(state.diagnostics_report_export_inflight),
    )
    setattr(app, "_backup_bundle_async_io", bool(state.backup_bundle_async_io))
    setattr(
        app,
        "_backup_bundle_export_inflight",
        bool(state.backup_bundle_export_inflight),
    )
    setattr(
        app,
        "_backup_bundle_import_inflight",
        bool(state.backup_bundle_import_inflight),
    )
    return state


def get_diagnostics_export_runtime_state(app: object) -> DiagnosticsExportRuntimeState:
    state = getattr(app, "_diagnostics_export_runtime_state", None)
    if not isinstance(state, DiagnosticsExportRuntimeState):
        state = DiagnosticsExportRuntimeState()
    sync_diagnostics_export_runtime_state_from_app(app, state)
    sync_diagnostics_export_runtime_state_to_app(app, state)
    return state


def set_diagnostics_bundle_export_inflight(app: object, inflight: bool) -> None:
    state = get_diagnostics_export_runtime_state(app)
    state.diagnostics_bundle_export_inflight = bool(inflight)
    sync_diagnostics_export_runtime_state_to_app(app, state)


def set_diagnostics_report_export_inflight(app: object, inflight: bool) -> None:
    state = get_diagnostics_export_runtime_state(app)
    state.diagnostics_report_export_inflight = bool(inflight)
    sync_diagnostics_export_runtime_state_to_app(app, state)


def set_backup_bundle_export_inflight(app: object, inflight: bool) -> None:
    state = get_diagnostics_export_runtime_state(app)
    state.backup_bundle_export_inflight = bool(inflight)
    sync_diagnostics_export_runtime_state_to_app(app, state)


def set_backup_bundle_import_inflight(app: object, inflight: bool) -> None:
    state = get_diagnostics_export_runtime_state(app)
    state.backup_bundle_import_inflight = bool(inflight)
    sync_diagnostics_export_runtime_state_to_app(app, state)
