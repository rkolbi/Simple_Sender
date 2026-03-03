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

import logging
import json
import os
import shutil
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.macro_files import discover_macro_assets, get_writable_macro_dir
from simple_sender.utils.task_timing import record_task_timing

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _bundle_default_dir() -> Path:
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        return desktop
    return Path.home()


def _bundle_filename() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"simple_sender_bundle_{timestamp}.zip"


def _safe_bundle_name(name: str) -> str:
    base = os.path.basename(name).strip()
    if not base:
        return ""
    if "/" in base or "\\" in base:
        return ""
    return base


def _post_ui_callback(app: Any, callback) -> None:
    after = getattr(app, "after", None)
    if callable(after):
        try:
            after(0, callback)
            return
        except Exception as exc:
            _log_suppressed("Failed posting backup-bundle callback to UI thread", exc)
    try:
        callback()
    except Exception as exc:
        _log_suppressed("Failed running backup-bundle callback", exc)


def _write_backup_bundle_archive(
    out_path: str,
    *,
    settings_path: str,
    app_version: str,
    assets: list[tuple[str, str]],
    created: str,
) -> None:
    manifest = {
        "kind": "simple_sender_backup_bundle",
        "created": created,
        "version": app_version,
        "files": {
            "settings": bool(settings_path and os.path.isfile(settings_path)),
            "macros": [name for _src, name in assets],
        },
    }
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        if settings_path and os.path.isfile(settings_path):
            archive.write(settings_path, arcname="settings/settings.json")
        for source, name in assets:
            safe_name = _safe_bundle_name(name)
            if not safe_name:
                continue
            archive.write(source, arcname=f"macros/{safe_name}")


def _import_backup_bundle_archive(
    in_path: str,
    *,
    settings_target: str,
    macro_dir: str | None,
) -> tuple[bool, int, int]:
    imported_settings = False
    imported_macros = 0
    macro_files_in_bundle = 0
    with zipfile.ZipFile(in_path, "r") as archive:
        members = archive.namelist()
        if "manifest.json" not in members:
            raise ValueError("Not a Simple Sender backup bundle (manifest.json missing).")
        with archive.open("manifest.json") as manifest_src:
            manifest = json.load(manifest_src)
        if not isinstance(manifest, dict) or manifest.get("kind") != "simple_sender_backup_bundle":
            raise ValueError("Unsupported backup bundle format.")
        if "settings/settings.json" in members and settings_target:
            os.makedirs(os.path.dirname(settings_target), exist_ok=True)
            with archive.open("settings/settings.json") as src, open(
                settings_target,
                "wb",
            ) as dst:
                shutil.copyfileobj(src, dst)
            imported_settings = True
        for member in members:
            if not member.startswith("macros/"):
                continue
            basename = _safe_bundle_name(member.split("/", 1)[1] if "/" in member else member)
            if not basename:
                continue
            macro_files_in_bundle += 1
            if macro_dir is None:
                continue
            target = os.path.join(macro_dir, basename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            imported_macros += 1
    return imported_settings, imported_macros, macro_files_in_bundle


def export_backup_bundle(app: Any) -> None:
    use_background_io = bool(getattr(app, "_backup_bundle_async_io", True))
    if use_background_io and bool(getattr(app, "_backup_bundle_export_inflight", False)):
        messagebox.showinfo("Backup bundle", "A backup-bundle export is already running.")
        return
    try:
        app._save_settings()
    except Exception as exc:
        _log_suppressed("Failed saving settings before backup-bundle export", exc)
    path = run_file_dialog(
        app,
        filedialog.asksaveasfilename,
        title="Export backup bundle",
        defaultextension=".zip",
        initialdir=str(_bundle_default_dir()),
        initialfile=_bundle_filename(),
        filetypes=(("Zip files", "*.zip"), ("All files", "*.*")),
    )
    if not path:
        return

    settings_path = str(getattr(app, "settings_path", "") or "")
    created = datetime.now().isoformat(timespec="seconds")
    app_version = str(getattr(getattr(app, "version_var", None), "get", lambda: "")() or "")
    started_at = time.perf_counter()
    if use_background_io and callable(getattr(app, "after", None)):
        app._backup_bundle_export_inflight = True
        try:
            app.ui_q.put(("log", "[diagnostics] Exporting backup bundle..."))
        except Exception:
            pass

        def _complete_export(error: Exception | None = None) -> None:
            app._backup_bundle_export_inflight = False
            elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            record_task_timing(app, "backup_bundle.export", elapsed_ms, success=(error is None))
            if error is not None:
                messagebox.showerror("Backup bundle", f"Failed to export bundle:\n{error}")
                return
            try:
                app.status.config(text=f"Backup bundle exported: {os.path.basename(path)}")
            except Exception as exc:
                _log_suppressed("Failed updating status after backup-bundle export", exc)
            messagebox.showinfo("Backup bundle", f"Bundle saved:\n{path}")

        def _worker() -> None:
            error: Exception | None = None
            try:
                assets = discover_macro_assets(app)
                _write_backup_bundle_archive(
                    path,
                    settings_path=settings_path,
                    app_version=app_version,
                    assets=assets,
                    created=created,
                )
            except Exception as exc:
                error = exc
            _post_ui_callback(app, lambda: _complete_export(error))

        try:
            worker = threading.Thread(
                target=_worker,
                name="backup-bundle-export",
                daemon=True,
            )
            worker.start()
            return
        except Exception as exc:
            app._backup_bundle_export_inflight = False
            _log_suppressed("Failed starting backup-bundle export thread", exc)
    try:
        assets = discover_macro_assets(app)
        _write_backup_bundle_archive(
            path,
            settings_path=settings_path,
            app_version=app_version,
            assets=assets,
            created=created,
        )
    except Exception as exc:
        elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "backup_bundle.export", elapsed_ms, success=False)
        messagebox.showerror("Backup bundle", f"Failed to export bundle:\n{exc}")
        return
    elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    record_task_timing(app, "backup_bundle.export", elapsed_ms, success=True)
    try:
        app.status.config(text=f"Backup bundle exported: {os.path.basename(path)}")
    except Exception as exc:
        _log_suppressed("Failed updating status after backup-bundle export", exc)
    messagebox.showinfo("Backup bundle", f"Bundle saved:\n{path}")


def import_backup_bundle(app: Any) -> None:
    use_background_io = bool(getattr(app, "_backup_bundle_async_io", True))
    if use_background_io and bool(getattr(app, "_backup_bundle_import_inflight", False)):
        messagebox.showinfo("Backup bundle", "A backup-bundle import is already running.")
        return
    path = run_file_dialog(
        app,
        filedialog.askopenfilename,
        title="Import backup bundle",
        initialdir=str(_bundle_default_dir()),
        filetypes=(("Zip files", "*.zip"), ("All files", "*.*")),
    )
    if not path:
        return
    if not messagebox.askyesno(
        "Import backup bundle",
        "Import settings and macro assets from this bundle?\n\n"
        "Imported settings apply fully after restarting the app.",
    ):
        return

    settings_target = str(getattr(app, "settings_path", "") or "")
    macro_dir = get_writable_macro_dir(app)
    started_at = time.perf_counter()

    def _apply_import_result(
        imported_settings: bool,
        imported_macros: int,
        macro_files_in_bundle: int,
    ) -> None:
        if imported_macros:
            try:
                panel = getattr(app, "macro_panel", None)
                if panel is not None and hasattr(panel, "refresh"):
                    panel.refresh()
            except Exception as exc:
                _log_suppressed("Failed refreshing macro panel after backup-bundle import", exc)
        notes: list[str] = []
        notes.append(f"Settings imported: {'yes' if imported_settings else 'no'}")
        if macro_files_in_bundle and macro_dir is None:
            notes.append("Macro assets skipped: no writable macro directory was found.")
        else:
            notes.append(f"Macro assets imported: {imported_macros}")
        notes.append("Restart the app to fully apply imported settings/checklists.")
        try:
            app.status.config(text=f"Backup bundle imported: {os.path.basename(path)}")
        except Exception as exc:
            _log_suppressed("Failed updating status after backup-bundle import", exc)
        messagebox.showinfo("Backup bundle", "\n".join(notes))

    if use_background_io and callable(getattr(app, "after", None)):
        app._backup_bundle_import_inflight = True
        try:
            app.ui_q.put(("log", "[diagnostics] Importing backup bundle..."))
        except Exception:
            pass

        def _complete_import(
            result: tuple[bool, int, int] | None,
            error: Exception | None = None,
        ) -> None:
            app._backup_bundle_import_inflight = False
            elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            record_task_timing(app, "backup_bundle.import", elapsed_ms, success=(error is None))
            if error is not None or result is None:
                messagebox.showerror("Backup bundle", f"Failed to import bundle:\n{error}")
                return
            _apply_import_result(*result)

        def _worker() -> None:
            error: Exception | None = None
            result: tuple[bool, int, int] | None = None
            try:
                result = _import_backup_bundle_archive(
                    path,
                    settings_target=settings_target,
                    macro_dir=macro_dir,
                )
            except Exception as exc:
                error = exc
            _post_ui_callback(app, lambda: _complete_import(result, error))

        try:
            worker = threading.Thread(
                target=_worker,
                name="backup-bundle-import",
                daemon=True,
            )
            worker.start()
            return
        except Exception as exc:
            app._backup_bundle_import_inflight = False
            _log_suppressed("Failed starting backup-bundle import thread", exc)

    try:
        result = _import_backup_bundle_archive(
            path,
            settings_target=settings_target,
            macro_dir=macro_dir,
        )
    except Exception as exc:
        elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "backup_bundle.import", elapsed_ms, success=False)
        messagebox.showerror("Backup bundle", f"Failed to import bundle:\n{exc}")
        return
    elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    record_task_timing(app, "backup_bundle.import", elapsed_ms, success=True)
    _apply_import_result(*result)
