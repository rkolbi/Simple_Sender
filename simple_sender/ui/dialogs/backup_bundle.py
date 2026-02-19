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
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.macro_files import discover_macro_assets, get_writable_macro_dir


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


def export_backup_bundle(app: Any) -> None:
    try:
        app._save_settings()
    except Exception:
        pass
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

    assets = discover_macro_assets(app)
    settings_path = str(getattr(app, "settings_path", "") or "")
    created = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "kind": "simple_sender_backup_bundle",
        "created": created,
        "version": str(getattr(getattr(app, "version_var", None), "get", lambda: "")() or ""),
        "files": {
            "settings": bool(settings_path and os.path.isfile(settings_path)),
            "macros": [name for _src, name in assets],
        },
    }
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            if settings_path and os.path.isfile(settings_path):
                archive.write(settings_path, arcname="settings/settings.json")
            for source, name in assets:
                safe_name = _safe_bundle_name(name)
                if not safe_name:
                    continue
                archive.write(source, arcname=f"macros/{safe_name}")
    except Exception as exc:
        messagebox.showerror("Backup bundle", f"Failed to export bundle:\n{exc}")
        return
    try:
        app.status.config(text=f"Backup bundle exported: {os.path.basename(path)}")
    except Exception:
        pass
    messagebox.showinfo("Backup bundle", f"Bundle saved:\n{path}")


def import_backup_bundle(app: Any) -> None:
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
    imported_settings = False
    imported_macros = 0
    macro_files_in_bundle = 0

    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.namelist()
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
    except Exception as exc:
        messagebox.showerror("Backup bundle", f"Failed to import bundle:\n{exc}")
        return

    if imported_macros:
        try:
            panel = getattr(app, "macro_panel", None)
            if panel is not None and hasattr(panel, "refresh"):
                panel.refresh()
        except Exception:
            pass

    notes: list[str] = []
    notes.append(f"Settings imported: {'yes' if imported_settings else 'no'}")
    if macro_files_in_bundle and macro_dir is None:
        notes.append("Macro assets skipped: no writable macro directory was found.")
    else:
        notes.append(f"Macro assets imported: {imported_macros}")
    notes.append("Restart the app to fully apply imported settings/checklists.")
    try:
        app.status.config(text=f"Backup bundle imported: {os.path.basename(path)}")
    except Exception:
        pass
    messagebox.showinfo("Backup bundle", "\n".join(notes))
