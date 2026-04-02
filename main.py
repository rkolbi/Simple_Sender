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
import time


def _notify_duplicate_instance(exc: BaseException) -> None:
    root = None
    try:
        import tkinter as tk
        from tkinter import messagebox

        title = str(getattr(exc, "title", "Simple Sender Already Running") or "Simple Sender Already Running")
        message = str(exc).strip() or "Another Simple Sender instance is already running."
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        messagebox.showerror(title, message, parent=root)
    except Exception as notify_exc:
        logging.getLogger(__name__).warning(
            "Duplicate-start notification failed; continuing with log-only warning: %s",
            notify_exc,
        )
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def main() -> None:
    startup_started_at = time.perf_counter()
    try:
        from simple_sender.utils.logging_config import setup_logging

        setup_logging()
    except Exception as exc:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        logging.getLogger(__name__).warning(
            "Logging bootstrap failed; continuing with basic logging: %s",
            exc,
        )
    from simple_sender import __version__
    from simple_sender.utils.runtime_integrity import (
        DuplicateInstanceError,
        ensure_runtime_marker_claimed,
    )

    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        ensure_runtime_marker_claimed(
            script_dir,
            version=__version__,
        )
    except DuplicateInstanceError as exc:
        logging.getLogger(__name__).warning(
            "Another Simple Sender instance is already running; exiting duplicate startup. pid=%s host=%s",
            exc.payload.get("pid"),
            exc.payload.get("hostname"),
        )
        _notify_duplicate_instance(exc)
        return
    from simple_sender.application import App
    App(startup_started_at=startup_started_at).mainloop()


if __name__ == "__main__":
    main()
