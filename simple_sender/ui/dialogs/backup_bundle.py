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
import threading
import time
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from simple_sender.ui.dialogs.file_dialogs import run_file_dialog
from simple_sender.ui.macro_files import discover_macro_assets, get_writable_macro_dir
from simple_sender.utils.atomic_files import atomic_replace_path, atomic_write_bytes
from simple_sender.utils.config import Settings
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


@dataclass
class _BundleInspection:
    has_settings: bool
    asset_names: list[str] = field(default_factory=list)
    colliding_assets: list[str] = field(default_factory=list)


@dataclass
class _BundleImportResult:
    imported_settings: bool
    repaired_keys: list[str] = field(default_factory=list)
    imported_macros: int = 0
    macro_files_in_bundle: int = 0
    overwritten_assets: list[str] = field(default_factory=list)


def _post_ui_callback(app: Any, callback) -> None:
    poster = getattr(app, "_post_ui_thread", None)
    if callable(poster):
        try:
            poster(callback)
            return
        except Exception as exc:
            _log_suppressed("Failed posting backup-bundle callback via _post_ui_thread", exc)
    after = getattr(app, "after", None)
    if callable(after):
        try:
            after(0, callback)
            return
        except Exception as exc:
            _log_suppressed("Failed posting backup-bundle callback to UI thread", exc)
    ui_q = getattr(app, "ui_q", None)
    if ui_q is not None:
        try:
            ui_q.put(("ui_post", callback, (), {}))
            return
        except Exception as exc:
            _log_suppressed("Failed posting backup-bundle callback via ui_q", exc)
    _log_suppressed(
        "Dropping backup-bundle callback because no safe UI post path is available",
        RuntimeError("ui thread unavailable"),
    )


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
    out_path_obj = Path(out_path)
    temp_path: Path | None = None
    try:
        out_path_obj.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(out_path_obj.parent),
            prefix=f"{out_path_obj.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            if settings_path and os.path.isfile(settings_path):
                archive.write(settings_path, arcname="settings/settings.json")
            for source, name in assets:
                safe_name = _safe_bundle_name(name)
                if not safe_name:
                    continue
                archive.write(source, arcname=f"macros/{safe_name}")
        assert temp_path is not None
        atomic_replace_path(temp_path, out_path_obj)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _read_bundle_members(in_path: str) -> tuple[bool, list[str]]:
    asset_names: list[str] = []
    has_settings = False
    with zipfile.ZipFile(in_path, "r") as archive:
        members = archive.namelist()
        if "manifest.json" not in members:
            raise ValueError("Not a Simple Sender backup bundle (manifest.json missing).")
        with archive.open("manifest.json") as manifest_src:
            manifest = json.load(manifest_src)
        if not isinstance(manifest, dict) or manifest.get("kind") != "simple_sender_backup_bundle":
            raise ValueError("Unsupported backup bundle format.")
        has_settings = "settings/settings.json" in members
        for member in members:
            if not member.startswith("macros/"):
                continue
            basename = _safe_bundle_name(member.split("/", 1)[1] if "/" in member else member)
            if basename:
                asset_names.append(basename)
    return has_settings, asset_names


def _inspect_backup_bundle(in_path: str, *, macro_dir: str | None) -> _BundleInspection:
    has_settings, asset_names = _read_bundle_members(in_path)
    colliding_assets: list[str] = []
    if macro_dir is not None:
        for name in asset_names:
            if os.path.exists(os.path.join(macro_dir, name)):
                colliding_assets.append(name)
    return _BundleInspection(
        has_settings=has_settings,
        asset_names=asset_names,
        colliding_assets=colliding_assets,
    )


def _restore_imported_asset_state(target_path: str, previous_bytes: bytes | None) -> None:
    target = Path(target_path)
    if previous_bytes is None:
        if target.exists():
            target.unlink()
        return
    atomic_write_bytes(target, previous_bytes)


def _import_backup_bundle_archive(
    in_path: str,
    *,
    settings_target: str,
    macro_dir: str | None,
    allow_overwrite: bool = False,
) -> _BundleImportResult:
    imported_store: Settings | None = None
    repaired_keys: list[str] = []
    staged_assets: list[tuple[str, bytes]] = []
    macro_files_in_bundle = 0
    overwritten_assets: list[str] = []
    committed_assets: list[tuple[str, bytes | None]] = []
    with tempfile.TemporaryDirectory(prefix="simple_sender_bundle_import_") as temp_dir:
        temp_settings_path = os.path.join(temp_dir, "settings.json")
        with zipfile.ZipFile(in_path, "r") as archive:
            members = archive.namelist()
            if "manifest.json" not in members:
                raise ValueError("Not a Simple Sender backup bundle (manifest.json missing).")
            with archive.open("manifest.json") as manifest_src:
                manifest = json.load(manifest_src)
            if not isinstance(manifest, dict) or manifest.get("kind") != "simple_sender_backup_bundle":
                raise ValueError("Unsupported backup bundle format.")
            if "settings/settings.json" in members and settings_target:
                with archive.open("settings/settings.json") as src:
                    atomic_write_bytes(temp_settings_path, src.read())
                imported_store = Settings(settings_target)
                repaired_keys = imported_store.import_from_file(temp_settings_path)
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
                if os.path.exists(target):
                    overwritten_assets.append(basename)
                    if not allow_overwrite:
                        raise ValueError(f"Bundle import would overwrite existing asset: {basename}")
                with archive.open(member) as src:
                    staged_assets.append((target, src.read()))

        try:
            for target, payload in staged_assets:
                previous_bytes = Path(target).read_bytes() if os.path.exists(target) else None
                committed_assets.append((target, previous_bytes))
                atomic_write_bytes(target, payload)
            if imported_store is not None:
                imported_store.save()
        except Exception:
            for target, previous_bytes in reversed(committed_assets):
                try:
                    _restore_imported_asset_state(target, previous_bytes)
                except Exception as rollback_exc:
                    _log_suppressed("Failed rolling back partially imported backup-bundle asset", rollback_exc)
            raise
    return _BundleImportResult(
        imported_settings=imported_store is not None,
        repaired_keys=list(repaired_keys),
        imported_macros=len(staged_assets),
        macro_files_in_bundle=macro_files_in_bundle,
        overwritten_assets=sorted(set(overwritten_assets), key=str.lower),
    )


def export_backup_bundle(app: Any) -> None:
    use_background_io = bool(getattr(app, "_backup_bundle_async_io", True))
    if use_background_io and bool(getattr(app, "_backup_bundle_export_inflight", False)):
        messagebox.showinfo("Backup bundle", "A backup-bundle export is already running.")
        return
    settings_save_error = ""
    try:
        app._save_settings()
    except Exception as exc:
        settings_save_error = str(exc).strip() or "Settings save failed."
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
                if settings_save_error:
                    app.status.config(
                        text=(
                            "Backup bundle exported with last-saved settings only: "
                            f"{os.path.basename(path)}"
                        )
                    )
                else:
                    app.status.config(text=f"Backup bundle exported: {os.path.basename(path)}")
            except Exception as exc:
                _log_suppressed("Failed updating status after backup-bundle export", exc)
            if settings_save_error:
                messagebox.showwarning(
                    "Backup bundle",
                    "Bundle saved with last-saved on-disk settings only.\n\n"
                    f"Latest settings could not be saved before export:\n{settings_save_error}\n\n"
                    f"Bundle saved:\n{path}",
                )
            else:
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
        if settings_save_error:
            app.status.config(
                text=f"Backup bundle exported with last-saved settings only: {os.path.basename(path)}"
            )
        else:
            app.status.config(text=f"Backup bundle exported: {os.path.basename(path)}")
    except Exception as exc:
        _log_suppressed("Failed updating status after backup-bundle export", exc)
    if settings_save_error:
        messagebox.showwarning(
            "Backup bundle",
            "Bundle saved with last-saved on-disk settings only.\n\n"
            f"Latest settings could not be saved before export:\n{settings_save_error}\n\n"
            f"Bundle saved:\n{path}",
        )
    else:
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

    settings_target = str(getattr(app, "settings_path", "") or "")
    macro_dir = get_writable_macro_dir(app)
    try:
        inspection = _inspect_backup_bundle(path, macro_dir=macro_dir)
    except Exception as exc:
        messagebox.showerror("Backup bundle", f"Failed to inspect bundle:\n{exc}")
        return
    prompt = [
        "Import settings and macro assets from this bundle?",
        "",
        "Imported settings apply fully after restarting the app.",
    ]
    if inspection.colliding_assets:
        preview = ", ".join(inspection.colliding_assets[:5])
        if len(inspection.colliding_assets) > 5:
            preview = f"{preview}, ..."
        prompt.extend(
            [
                "",
                "This bundle will overwrite existing macro/checklist assets:",
                preview,
                "",
                "Continue?",
            ]
        )
    if not messagebox.askyesno("Import backup bundle", "\n".join(prompt)):
        return
    started_at = time.perf_counter()

    def _apply_import_result(result: _BundleImportResult) -> None:
        if result.imported_macros:
            try:
                panel = getattr(app, "macro_panel", None)
                if panel is not None and hasattr(panel, "refresh"):
                    panel.refresh()
            except Exception as exc:
                _log_suppressed("Failed refreshing macro panel after backup-bundle import", exc)
        notes: list[str] = []
        notes.append(f"Settings imported: {'yes' if result.imported_settings else 'no'}")
        if result.repaired_keys:
            notes.append(f"Imported settings repaired to defaults for: {', '.join(result.repaired_keys)}")
        if result.macro_files_in_bundle and macro_dir is None:
            notes.append("Macro assets skipped: no writable macro directory was found.")
        else:
            notes.append(f"Macro assets imported: {result.imported_macros}")
        if result.overwritten_assets:
            notes.append(
                "Replaced existing assets: "
                + ", ".join(result.overwritten_assets[:5])
                + (", ..." if len(result.overwritten_assets) > 5 else "")
            )
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
            result: _BundleImportResult | None,
            error: Exception | None = None,
        ) -> None:
            app._backup_bundle_import_inflight = False
            elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
            record_task_timing(app, "backup_bundle.import", elapsed_ms, success=(error is None))
            if error is not None or result is None:
                messagebox.showerror("Backup bundle", f"Failed to import bundle:\n{error}")
                return
            _apply_import_result(result)

        def _worker() -> None:
            error: Exception | None = None
            result: _BundleImportResult | None = None
            try:
                result = _import_backup_bundle_archive(
                    path,
                    settings_target=settings_target,
                    macro_dir=macro_dir,
                    allow_overwrite=bool(inspection.colliding_assets),
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
            allow_overwrite=bool(inspection.colliding_assets),
        )
    except Exception as exc:
        elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
        record_task_timing(app, "backup_bundle.import", elapsed_ms, success=False)
        messagebox.showerror("Backup bundle", f"Failed to import bundle:\n{exc}")
        return
    elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    record_task_timing(app, "backup_bundle.import", elapsed_ms, success=True)
    _apply_import_result(result)
