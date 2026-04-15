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

"""Diagnostics bundle export helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from simple_sender.ui.diagnostics_export_runtime_state import (
    get_diagnostics_export_runtime_state,
    set_diagnostics_bundle_export_inflight,
)


def _post_ui_callback(
    app: Any,
    callback,
    *,
    log_suppressed: Callable[[str, BaseException], None],
    on_drop: Callable[[], None] | None = None,
) -> bool:
    poster = getattr(app, "_post_ui_thread", None)
    if callable(poster):
        try:
            poster(callback)
            return True
        except Exception as exc:
            log_suppressed(
                "Failed posting diagnostics bundle callback via _post_ui_thread",
                exc,
            )
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", callback, (), {}))
            return True
        except Exception as exc:
            log_suppressed("Failed posting diagnostics bundle callback via ui_q", exc)
    log_suppressed(
        "Dropping diagnostics bundle callback because no safe UI post path is available",
        RuntimeError("ui thread unavailable"),
    )
    if callable(on_drop):
        try:
            on_drop()
        except Exception as exc:
            log_suppressed(
                "Failed running diagnostics bundle drop-path cleanup",
                exc,
            )
    return False


def export_diagnostics_bundle(
    app: Any,
    *,
    run_file_dialog: Callable[..., Any],
    asksaveasfilename: Callable[..., Any],
    collect_diagnostics_bundle_payload: Callable[[Any], dict[str, Any]],
    write_diagnostics_bundle_archive: Callable[[Path, dict[str, Any]], None],
    log_suppressed: Callable[[str, BaseException], None],
    showinfo: Callable[[str, str], None],
    showerror: Callable[[str, str], None],
    thread_cls: Any,
) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"simple_sender_diagnostics_bundle_{timestamp}.zip"
    path = run_file_dialog(
        app,
        asksaveasfilename,
        title="Export diagnostics bundle",
        defaultextension=".zip",
        initialfile=default_name,
        filetypes=(("Zip files", "*.zip"), ("All files", "*.*")),
    )
    if not path:
        return
    out_path = Path(path)
    export_state = get_diagnostics_export_runtime_state(app)
    use_background_export = bool(export_state.diagnostics_bundle_async_export)
    if use_background_export and callable(getattr(app, "after", None)):
        if bool(export_state.diagnostics_bundle_export_inflight):
            showinfo(
                "Export diagnostics bundle",
                "A diagnostics bundle export is already running.",
            )
            return
        set_diagnostics_bundle_export_inflight(app, True)
        try:
            app.ui_q.put(("log", "[diagnostics] Exporting diagnostics bundle..."))
        except Exception:
            pass

        def _complete_export(error: Exception | None = None) -> None:
            set_diagnostics_bundle_export_inflight(app, False)
            if error is None:
                showinfo("Export diagnostics bundle", f"Saved to:\n{out_path}")
                return
            showerror(
                "Export diagnostics bundle", f"Failed to create bundle:\n{error}"
            )

        def _export_worker() -> None:
            error: Exception | None = None
            try:
                if out_path.parent:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                payload = collect_diagnostics_bundle_payload(app)
                write_diagnostics_bundle_archive(out_path, payload)
            except Exception as exc:
                log_suppressed(
                    "Failed exporting diagnostics bundle in background worker", exc
                )
                error = exc
            _post_ui_callback(
                app,
                lambda: _complete_export(error),
                log_suppressed=log_suppressed,
                on_drop=lambda: set_diagnostics_bundle_export_inflight(app, False),
            )

        try:
            worker = thread_cls(
                target=_export_worker,
                name="diagnostics-bundle-export",
                daemon=True,
            )
            worker.start()
            return
        except Exception as exc:
            set_diagnostics_bundle_export_inflight(app, False)
            log_suppressed("Failed starting diagnostics bundle export thread", exc)

    try:
        if out_path.parent:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = collect_diagnostics_bundle_payload(app)
        write_diagnostics_bundle_archive(out_path, payload)
        showinfo("Export diagnostics bundle", f"Saved to:\n{out_path}")
    except Exception as exc:
        showerror("Export diagnostics bundle", f"Failed to create bundle:\n{exc}")
