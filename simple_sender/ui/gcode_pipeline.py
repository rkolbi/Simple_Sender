import os
import threading
from tkinter import messagebox

from simple_sender.gcode_parser import clean_gcode_line, parse_gcode_lines, split_gcode_lines
from simple_sender.gcode_validator import validate_gcode_lines
from simple_sender.utils.constants import GCODE_VIEWER_CHUNK_SIZE_SMALL, MAX_LINE_LENGTH
from simple_sender.utils.hashing import hash_lines


def load_gcode_from_path(app, path: str):
    if app.grbl.is_streaming():
        messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
        return
    if not os.path.isfile(path):
        messagebox.showerror("Open G-code", "File not found.")
        return
    app.settings["last_gcode_dir"] = os.path.dirname(path)
    app._gcode_load_token += 1
    token = app._gcode_load_token
    app._gcode_loading = True
    app.btn_run.config(state="disabled")
    app.btn_pause.config(state="disabled")
    app.btn_resume.config(state="disabled")
    app.btn_resume_from.config(state="disabled")
    app.gcode_stats_var.set("Loading...")
    app.status.config(text=f"Loading: {os.path.basename(path)}")
    app._set_gcode_loading_indeterminate(f"Reading {os.path.basename(path)}")
    app.gview.set_lines_chunked([])

    def worker():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = []
                line_map = []
                total_lines = 0
                for line_no, ln in enumerate(f, start=1):
                    total_lines = line_no
                    cleaned = clean_gcode_line(ln)
                    if not cleaned:
                        continue
                    lines.append(cleaned)
                    line_map.append(line_no)
            result = split_gcode_lines(lines, MAX_LINE_LENGTH)
            if result.failed_index is not None:
                too_long = 0
                first_idx = None
                first_len = None
                for idx, line in enumerate(lines):
                    line_len = len(line.encode("utf-8")) + 1
                    if line_len > MAX_LINE_LENGTH:
                        too_long += 1
                        if first_idx is None:
                            first_idx = idx
                            first_len = line_len
                if too_long == 0:
                    too_long = 1
                    first_idx = result.failed_index
                    first_len = result.failed_len
                if first_idx is not None and 0 <= first_idx < len(line_map):
                    first_idx = line_map[first_idx] - 1
                app.ui_q.put((
                    "gcode_load_invalid",
                    token,
                    path,
                    too_long,
                    first_idx,
                    first_len,
                    total_lines,
                    len(lines),
                ))
                return
            if result.modified_count:
                if result.split_count:
                    msg = (
                        f"[gcode] Adjusted {result.modified_count} line(s) to fit "
                        f"{MAX_LINE_LENGTH}-byte limit (split {result.split_count})."
                    )
                else:
                    msg = (
                        f"[gcode] Adjusted {result.modified_count} line(s) to fit "
                        f"{MAX_LINE_LENGTH}-byte limit."
                    )
                app.ui_q.put(("log", msg))
            lines = result.lines
            report = validate_gcode_lines(lines)
            lines_hash = hash_lines(lines)
            app.ui_q.put(("gcode_loaded", token, path, lines, lines_hash, True, report))
        except Exception as exc:
            app.ui_q.put(("gcode_load_error", token, path, str(exc)))

    threading.Thread(target=worker, daemon=True).start()


def apply_loaded_gcode(
    app,
    path: str,
    lines: list[str],
    *,
    lines_hash: str | None = None,
    validated: bool = False,
):
    if app.grbl.is_streaming():
        app._gcode_loading = False
        app._finish_gcode_loading()
        messagebox.showwarning("Busy", "Stop the stream before loading a new G-code file.")
        app.status.config(text="G-code load skipped (streaming)")
        return
    if not validated:
        result = split_gcode_lines(lines, MAX_LINE_LENGTH)
        if result.failed_index is not None:
            max_len = MAX_LINE_LENGTH
            too_long = 0
            first_idx = None
            first_len = None
            for idx, line in enumerate(lines):
                line_len = len(line.encode("utf-8")) + 1
                if line_len > max_len:
                    too_long += 1
                    if first_idx is None:
                        first_idx = idx
                        first_len = line_len
            if too_long == 0:
                too_long = 1
                first_idx = result.failed_index
                first_len = result.failed_len
            app._gcode_loading = False
            app._finish_gcode_loading()
            messagebox.showerror(
                "Open G-code",
                f"{too_long} non-empty line(s) exceed GRBL's {max_len}-byte limit.\n"
                f"First at line {first_idx + 1} ({first_len} bytes including newline).",
            )
            app.status.config(text="G-code load failed")
            return
        if result.modified_count:
            if result.split_count:
                msg = (
                    f"[gcode] Adjusted {result.modified_count} line(s) to fit "
                    f"{MAX_LINE_LENGTH}-byte limit (split {result.split_count})."
                )
            else:
                msg = (
                    f"[gcode] Adjusted {result.modified_count} line(s) to fit "
                    f"{MAX_LINE_LENGTH}-byte limit."
                )
            app.streaming_controller.log(msg)
        lines = result.lines
        lines_hash = hash_lines(lines)
    if app._gcode_validation_report is None:
        app._gcode_validation_report = validate_gcode_lines(lines)
    app._clear_pending_ui_updates()
    app._last_gcode_lines = lines
    app._last_gcode_path = path
    app._gcode_hash = lines_hash if lines_hash is not None else hash_lines(lines)
    app._stats_cache.clear()
    app._live_estimate_min = None
    app._last_stats = None
    app._last_rate_source = None
    app.grbl.load_gcode(lines, name=os.path.basename(path))
    app._last_sent_index = -1
    app._last_acked_index = -1
    app._last_parse_result = None
    app._last_parse_hash = None
    if bool(app.render3d_enabled.get()):
        app.toolpath_panel.set_enabled(True)
    else:
        app.toolpath_panel.set_enabled(False)
    app.toolpath_panel.clear()
    app.toolpath_panel.set_job_name(os.path.basename(path))
    if lines:
        app.gcode_stats_var.set("Calculating stats...")
        schedule_gcode_parse(app, lines, app._gcode_hash)
    else:
        app.gcode_stats_var.set("No file loaded")
    app.status.config(text=f"Loaded: {os.path.basename(path)}  ({len(lines)} lines)")

    name = os.path.basename(path)

    def on_done():
        app._gcode_loading = False
        app._finish_gcode_loading()
        if (
            app.connected
            and lines
            and app._grbl_ready
            and app._status_seen
            and not app._alarm_locked
        ):
            app.btn_run.config(state="normal")
            app.btn_resume_from.config(state="normal")
        else:
            app.btn_run.config(state="disabled")
            app.btn_resume_from.config(state="disabled")

    def on_progress(done, total):
        app._set_gcode_loading_progress(done, total, name)

    if not lines:
        app.gview.set_lines([])
        app._set_gcode_loading_progress(0, 0, name)
        on_done()
        return

    chunk_size = 300 if len(lines) > 2000 else GCODE_VIEWER_CHUNK_SIZE_SMALL
    app._set_gcode_loading_progress(0, len(lines), name)
    app.gview.set_lines_chunked(
        lines,
        chunk_size=chunk_size,
        on_done=on_done,
        on_progress=on_progress,
    )


def schedule_gcode_parse(app, lines: list[str], lines_hash: str | None):
    if not lines:
        app._last_parse_result = None
        app._last_parse_hash = None
        return
    app._gcode_parse_token += 1
    token = app._gcode_parse_token
    arc_step = app.toolpath_panel.get_arc_step_rad(len(lines))

    def worker():
        try:
            def keep_running():
                return token == app._gcode_parse_token

            result = parse_gcode_lines(lines, arc_step, keep_running=keep_running)
        except Exception as exc:
            app.ui_q.put(("log", f"[gcode] Parse failed: {exc}"))

            def apply_error():
                if token != app._gcode_parse_token:
                    return
                app._last_parse_result = None
                app._last_parse_hash = None
                app._stats_token += 1
                app._last_stats = None
                app._last_rate_source = None
                app.gcode_stats_var.set("Estimate unavailable")

            app.after(0, apply_error)
            return
        if result is None:
            return

        def apply_result():
            if token != app._gcode_parse_token:
                return
            app._last_parse_result = result
            app._last_parse_hash = lines_hash
            app.toolpath_panel.apply_parse_result(lines, result, lines_hash=lines_hash)
            app._update_gcode_stats(lines, parse_result=result)

        app.after(0, apply_result)

    threading.Thread(target=worker, daemon=True).start()


def clear_gcode(app):
    if app.grbl.is_streaming():
        messagebox.showwarning("Busy", "Stop the stream before clearing the G-code file.")
        return
    app._last_gcode_lines = []
    app._last_gcode_path = None
    app._gcode_hash = None
    app._gcode_validation_report = None
    app._last_parse_result = None
    app._last_parse_hash = None
    app._gcode_parse_token += 1
    app._stats_cache.clear()
    app.grbl.load_gcode([])
    app.gview.set_lines([])
    app.gcode_stats_var.set("No file loaded")
    app.progress_pct.set(0)
    app.status.config(text="G-code cleared")
    app.btn_run.config(state="disabled")
    app.btn_pause.config(state="disabled")
    app.btn_resume.config(state="disabled")
    app.btn_resume_from.config(state="disabled")
    app.toolpath_panel.clear()
    app._job_started_at = None
    app._job_completion_notified = False
