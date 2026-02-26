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
import os

logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()
_FILE_DIALOG_ACTIVE_ATTR = "_file_dialog_active"
_FILE_DIALOG_MIN_SCALE = 1.4
_FILE_DIALOG_MAX_SCALE = 3.0
_FILE_DIALOG_MIN_WIDTH = 540
_FILE_DIALOG_MIN_HEIGHT = 360
_FILE_DIALOG_RESIZE_POLL_MS = 60
_FILE_DIALOG_WINDOW_CLASSES = frozenset({"TkFDialog", "TkMotifFDialog", "TkChooseDir"})


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


def _resolve_parent(app, kwargs):
    parent = kwargs.get("parent")
    if parent is not None:
        return parent
    if app is None:
        return None
    try:
        if bool(app.winfo_exists()):
            return app
    except Exception:
        return None
    return None


def _coerce_scale(value, default: float = _FILE_DIALOG_MIN_SCALE) -> float:
    try:
        scale = float(value)
    except Exception:
        return default
    if scale <= 0:
        return default
    return max(1.0, min(_FILE_DIALOG_MAX_SCALE, scale))


def _dialog_target_scale(app, old_scale: float) -> float:
    ui_scale = _FILE_DIALOG_MIN_SCALE
    try:
        ui_scale = _coerce_scale(app.ui_scale.get(), _FILE_DIALOG_MIN_SCALE)
    except Exception:
        ui_scale = _FILE_DIALOG_MIN_SCALE
    return max(float(old_scale), float(ui_scale), _FILE_DIALOG_MIN_SCALE)


def _get_tk_var(app, name: str) -> str | None:
    try:
        return str(app.tk.call("set", name))
    except Exception:
        return None


def _set_tk_var(app, name: str, value: str) -> None:
    try:
        app.tk.call("set", name, value)
    except Exception as exc:
        _log_suppressed(f"Failed setting Tk variable {name}", exc)


def _dialog_parent_path(parent) -> str:
    if parent is None:
        return "."
    try:
        return str(parent.winfo_toplevel())
    except Exception:
        return "."


def _start_linux_dialog_resize_watch(app, *, parent):
    # Resize Tk-managed file dialogs on Linux so file lists remain usable.
    if not hasattr(app, "after"):
        return lambda: None

    root_path = _dialog_parent_path(parent)
    stop = {"done": False}
    after_id = {"value": None}
    resized: set[str] = set()

    def _tick() -> None:
        if stop["done"]:
            return
        try:
            children = app.tk.splitlist(app.tk.call("winfo", "children", root_path))
        except Exception:
            children = ()
        for child in children:
            child_path = str(child)
            if child_path in resized:
                continue
            try:
                cls = str(app.tk.call("winfo", "class", child_path))
            except Exception:
                continue
            if cls not in _FILE_DIALOG_WINDOW_CLASSES:
                continue
            resized.add(child_path)
            try:
                app.tk.call("wm", "minsize", child_path, _FILE_DIALOG_MIN_WIDTH, _FILE_DIALOG_MIN_HEIGHT)
                app.tk.call(
                    "wm",
                    "geometry",
                    child_path,
                    f"{_FILE_DIALOG_MIN_WIDTH}x{_FILE_DIALOG_MIN_HEIGHT}",
                )
            except Exception as exc:
                _log_suppressed("Failed resizing Linux file dialog window", exc)
        try:
            after_id["value"] = app.after(_FILE_DIALOG_RESIZE_POLL_MS, _tick)
        except Exception as exc:
            _log_suppressed("Failed scheduling Linux file dialog resize watcher", exc)

    try:
        _tick()
    except Exception as exc:
        _log_suppressed("Linux file dialog resize watcher failed to start", exc)

    def _stop() -> None:
        stop["done"] = True
        current_after = after_id.get("value")
        if current_after is None:
            return
        try:
            app.after_cancel(current_after)
        except Exception:
            return

    return _stop


def _run_with_dialog_scaling(app, func, *args, **kwargs):
    # Windows native dialogs became unstable when Tk scaling was changed at runtime.
    if os.name == "nt":
        return func(*args, **kwargs)
    old_motif = _get_tk_var(app, "tk_strictMotif")
    if old_motif is not None:
        # Prefer modern Tk file dialog implementation on X11.
        _set_tk_var(app, "tk_strictMotif", "0")
    stop_resize_watch = _start_linux_dialog_resize_watch(app, parent=kwargs.get("parent"))
    try:
        old_scale = float(app.tk.call("tk", "scaling"))
    except Exception:
        old_scale = None

    target_scale = None
    if old_scale is not None:
        target_scale = _dialog_target_scale(app, old_scale)
        if abs(target_scale - old_scale) >= 1e-6:
            try:
                app.tk.call("tk", "scaling", target_scale)
            except Exception as exc:
                _log_suppressed("Failed applying temporary Tk scaling for file dialog", exc)

    try:
        return func(*args, **kwargs)
    finally:
        try:
            stop_resize_watch()
        except Exception:
            pass
        if old_scale is not None and target_scale is not None and abs(target_scale - old_scale) >= 1e-6:
            try:
                app.tk.call("tk", "scaling", old_scale)
            except Exception as exc:
                _log_suppressed("Failed restoring Tk scaling after file dialog", exc)
        if old_motif is not None:
            _set_tk_var(app, "tk_strictMotif", old_motif)


def _run_dialog_once(app, func, *args, **kwargs):
    call_kwargs = dict(kwargs)
    parent = _resolve_parent(app, call_kwargs)
    if parent is not None and call_kwargs.get("parent") is None:
        call_kwargs["parent"] = parent

    guard_active = False
    if app is not None:
        try:
            if bool(getattr(app, _FILE_DIALOG_ACTIVE_ATTR, False)):
                return ""
            setattr(app, _FILE_DIALOG_ACTIVE_ATTR, True)
            guard_active = True
        except Exception:
            guard_active = False
    try:
        return _run_with_dialog_scaling(app, func, *args, **call_kwargs)
    finally:
        if guard_active:
            try:
                setattr(app, _FILE_DIALOG_ACTIVE_ATTR, False)
            except Exception as exc:
                _log_suppressed("Failed clearing file-dialog active guard flag", exc)


def run_file_dialog(app, func, *args, **kwargs):
    return _run_dialog_once(app, func, *args, **kwargs)
