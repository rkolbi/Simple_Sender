import logging
import math
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional

from simple_sender.gcode_parser import parse_gcode_lines
from simple_sender.ui.widgets import apply_tooltip, set_kb_id, _resolve_widget_bg
from simple_sender.utils.hashing import hash_lines as _hash_lines

logger = logging.getLogger(__name__)

_TOOLPATH_SEGMENT_COLORS = {
    "rapid": "#8a8a8a",
    "feed": "#2c6dd2",
    "arc": "#2aa876",
}

class ToolpathPanel:
    def __init__(self, app):
        self.app = app
        self.view: Toolpath3D | None = None
        self.top_view: Optional["TopViewPanel"] = None
        self.tab: ttk.Frame | None = None
        self._streaming = False
        self._pending_gcode_lines: list[str] | None = None
        self._pending_gcode_hash: str | None = None
        self._pending_top_lines: list[str] | None = None
        self._pending_parsed = None
        self._pending_top_parsed = None

    def build_tab(self, notebook: ttk.Notebook):
        top_tab = ttk.Frame(notebook, padding=6)
        notebook.add(top_tab, text="Top View")
        self.top_view = TopViewPanel(top_tab)
        self.top_view.pack(fill="both", expand=True)

        tab = ttk.Frame(notebook, padding=6)
        notebook.add(tab, text="3D View")
        self.tab = tab
        self.view = Toolpath3D(
            tab,
            on_save_view=self.app._save_3d_view,
            on_load_view=self.app._load_3d_view,
            perf_callback=self._toolpath_perf_logger,
        )
        self.view.pack(fill="both", expand=True)
        self._configure_view()
        self.view.set_streaming_mode(self._streaming)
        self.app._load_3d_view(show_status=False)
        if self._pending_parsed is not None and self.view:
            lines, result, lines_hash = self._pending_parsed
            self._pending_parsed = None
            self._pending_gcode_lines = None
            self._pending_gcode_hash = None
            self.view.apply_parsed_gcode(lines, result.segments, result.bounds, lines_hash=lines_hash)
        if self._pending_gcode_lines is not None and self.view and getattr(self.view, "_visible", True):
            lines = self._pending_gcode_lines
            lines_hash = self._pending_gcode_hash
            self._pending_gcode_lines = None
            self._pending_gcode_hash = None
            self.view.set_gcode_async(lines, lines_hash=lines_hash)
        if self._pending_top_parsed is not None and self.top_view:
            result, lines_hash = self._pending_top_parsed
            self._pending_top_parsed = None
            self._pending_top_lines = None
            self.top_view.apply_parsed_gcode(result.segments, result.bounds, lines_hash=lines_hash)
        if self._pending_top_lines is not None and self.top_view and getattr(self.top_view, "_visible", True):
            lines = self._pending_top_lines
            self._pending_top_lines = None
            self.top_view.set_lines(lines)

    def _configure_view(self):
        if not self.view:
            return
        self.view.set_display_options(
            rapid=bool(self.app.settings.get("toolpath_show_rapid", False)),
            feed=bool(self.app.settings.get("toolpath_show_feed", True)),
            arc=bool(self.app.settings.get("toolpath_show_arc", False)),
        )
        self.view.set_performance_controls(
            self.app.toolpath_performance,
            self.app._toolpath_performance_value,
            self.app._on_toolpath_performance_move,
            self.app._apply_toolpath_performance,
            self.app._on_toolpath_performance_key_release,
        )
        self.view.set_enabled(bool(self.app.render3d_enabled.get()))
        self.view.set_lightweight_mode(bool(self.app.toolpath_lightweight.get()))
        self.view.set_draw_limits(
            self.app._toolpath_limit_value(self.app.toolpath_full_limit.get(), self.app._toolpath_full_limit_default),
            self.app._toolpath_limit_value(self.app.toolpath_interactive_limit.get(), self.app._toolpath_interactive_limit_default),
        )
        draw_percent = getattr(self.app, "_toolpath_draw_percent", None)
        if draw_percent is None:
            draw_percent = self.app.settings.get("toolpath_draw_percent", 50)
        self.view.set_draw_percent(draw_percent)
        self.view.set_streaming_render_interval(self.app.toolpath_streaming_render_interval.get())
        self.view.set_arc_detail_override(math.radians(self.app.toolpath_arc_detail.get()))

    def _toolpath_perf_logger(self, label: str, duration: float):
        if duration < 0.05:
            return
        try:
            self.app.ui_q.put(("log", f"[toolpath] {label} took {duration:.2f}s"))
        except Exception:
            pass

    def get_arc_step_rad(self, line_count: int) -> float:
        if self.view:
            return self.view.select_arc_step_rad(line_count)
        return math.pi / 18

    def apply_parse_result(self, lines: list[str], result, lines_hash: str | None = None):
        if result is None:
            return
        self._pending_gcode_lines = None
        self._pending_gcode_hash = None
        self._pending_top_lines = None
        if self.view:
            self._pending_parsed = None
            self.view.apply_parsed_gcode(lines, result.segments, result.bounds, lines_hash=lines_hash)
        else:
            self._pending_parsed = (lines, result, lines_hash)
        if self.top_view:
            self._pending_top_parsed = None
            self.top_view.apply_parsed_gcode(result.segments, result.bounds, lines_hash=lines_hash)
        else:
            self._pending_top_parsed = (result, lines_hash)

    def set_gcode_lines(self, lines: list[str], lines_hash: str | None = None):
        self._pending_parsed = None
        if not self.view or not getattr(self.view, "_visible", True):
            self._pending_gcode_lines = lines
            self._pending_gcode_hash = lines_hash
            return
        self._pending_gcode_lines = None
        self._pending_gcode_hash = None
        self.view.set_gcode_async(lines, lines_hash=lines_hash)

    def set_top_view_lines(self, lines: list[str] | None):
        self._pending_top_parsed = None
        if not self.top_view or not getattr(self.top_view, "_visible", True):
            self._pending_top_lines = lines if lines else []
            return
        self._pending_top_lines = None
        self.top_view.set_lines(lines)

    def clear(self):
        self._pending_parsed = None
        self._pending_top_parsed = None
        self._pending_gcode_lines = None
        self._pending_gcode_hash = None
        self._pending_top_lines = None
        if self.view:
            self.view.set_gcode_async([])
            self.view.set_job_name("")
        if self.top_view:
            self.top_view.clear()

    def set_job_name(self, name: str):
        if self.view:
            self.view.set_job_name(name)
        if self.top_view:
            self.top_view.set_job_name(name)

    def set_visible(self, visible: bool):
        if self.view:
            self.view.set_visible(visible)
        # Keep the top view hidden only when its tab is not selected.
        if visible and self._pending_gcode_lines is not None and self.view:
            lines = self._pending_gcode_lines
            lines_hash = self._pending_gcode_hash
            self._pending_gcode_lines = None
            self._pending_gcode_hash = None
            self.view.set_gcode_async(lines, lines_hash=lines_hash)

    def set_top_view_visible(self, visible: bool):
        if self.top_view:
            self.top_view.set_visible(visible)
        if visible and self._pending_top_lines is not None and self.top_view:
            lines = self._pending_top_lines
            self._pending_top_lines = None
            self.top_view.set_lines(lines)

    def set_enabled(self, enabled: bool):
        if self.view:
            self.view.set_enabled(enabled)

    def set_lightweight(self, value: bool):
        if self.view:
            self.view.set_lightweight_mode(value)

    def set_draw_limits(self, full: int, interactive: int):
        if self.view:
            self.view.set_draw_limits(full, interactive)

    def set_arc_detail(self, deg: float):
        if self.view:
            self.view.set_arc_detail_override(math.radians(deg))

    def set_draw_percent(self, percent: int):
        if self.view:
            self.view.set_draw_percent(percent)

    def set_streaming_render_interval(self, interval: float):
        if self.view:
            self.view.set_streaming_render_interval(interval)

    def set_streaming(self, streaming: bool):
        self._streaming = bool(streaming)
        if self.view:
            self.view.set_streaming_mode(self._streaming)

    def reparse_lines(self, lines: list[str], lines_hash: str | None = None):
        if self.view:
            self.view.set_gcode_async(lines, lines_hash=lines_hash)

    def set_position(self, x: float, y: float, z: float):
        if self.view:
            self.view.set_position(x, y, z)

    def get_view_state(self):
        if self.view:
            return self.view.get_view()
        return None

    def apply_view_state(self, state):
        if self.view and state:
            self.view.apply_view(state)

    def get_draw_percent(self):
        if self.view:
            return self.view.get_draw_percent()
        return getattr(self.app, "_toolpath_draw_percent", 50)

    def get_display_options(self):
        if self.view:
            return self.view.get_display_options()
        return (False, False, False)


class Toolpath3D(ttk.Frame):
    def __init__(
        self,
        parent,
        on_save_view=None,
        on_load_view=None,
        perf_callback: Callable[[str, float], None] | None = None,
    ):
        super().__init__(parent)
        bg = "SystemButtonFace"
        try:
            bg = parent.cget("background")
        except Exception:
            pass
        self.show_rapid = tk.BooleanVar(value=False)
        self.show_feed = tk.BooleanVar(value=True)
        self.show_arc = tk.BooleanVar(value=False)
        self._draw_percent_default = 50
        self._draw_percent = self._draw_percent_default
        self._draw_percent_text = tk.StringVar(value=f"{self._draw_percent}%")

        self.on_save_view = on_save_view
        self.on_load_view = on_load_view

        legend = ttk.Frame(self)
        legend.pack(side="top", fill="x")
        self._legend_frame = legend
        self._perf_frame = None
        self._perf_scale = None
        self._perf_value_label = None
        self._legend_label(legend, "#8a8a8a", "Rapid", self.show_rapid)
        self._legend_label(legend, "#2c6dd2", "Feed", self.show_feed)
        self._legend_label(legend, "#2aa876", "Arc", self.show_arc)
        self.btn_reset_view = ttk.Button(legend, text="Reset View", command=self._reset_view)
        set_kb_id(self.btn_reset_view, "view_reset")
        self.btn_reset_view.pack(side="right", padx=(6, 0))
        self.btn_load_view = ttk.Button(legend, text="Load View", command=self._load_view)
        set_kb_id(self.btn_load_view, "view_load")
        self.btn_load_view.pack(side="right", padx=(6, 0))
        self.btn_save_view = ttk.Button(legend, text="Save View", command=self._save_view)
        set_kb_id(self.btn_save_view, "view_save")
        self.btn_save_view.pack(side="right", padx=(6, 0))
        apply_tooltip(self.btn_save_view, "Save the current 3D view.")
        apply_tooltip(self.btn_load_view, "Load the saved 3D view.")
        apply_tooltip(self.btn_reset_view, "Reset the 3D view.")

        self.canvas = tk.Canvas(self, background=bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan)
        self.canvas.bind("<Shift-ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<Shift-B1-Motion>", self._on_pan)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)

        self.segments: list[tuple[float, float, float, float, float, float, str]] = []
        self.bounds = None
        self.position = None
        self.azimuth = math.radians(45)
        self.elevation = math.radians(30)
        self.zoom = 1.0
        self._drag_start = None
        self._pan_start = None
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.enabled = True
        self._pending_lines = None
        self._pending_parsed = None
        self._parse_token = 0
        self._render_pending = False
        self._render_interval = 0.1
        self._streaming_render_interval = 0.25
        self._last_render_ts = 0.0
        self._visible = True
        self._colors = {
            "rapid": "#8a8a8a",
            "feed": "#2c6dd2",
            "arc": "#2aa876",
        }
        self._preview_target = 1000
        self._full_parse_limit = 20000
        self._arc_step_default = math.pi / 18
        self._arc_step_fast = math.pi / 12
        self._arc_step_large = math.pi / 8
        self._arc_step_rad = self._arc_step_default
        self._arc_step_override_rad = None
        self._max_draw_segments = 40000
        self._interactive_max_draw_segments = 5000
        self._fast_mode = False
        self._fast_mode_after_id = None
        self._fast_mode_duration = 0.3
        self._render_params = None
        self._position_item = None
        self._last_lines_hash = None
        self._last_segments = None
        self._last_bounds = None
        self._lightweight_mode = False
        self._lightweight_preview_target = 400
        self._job_name = ""
        self._cached_projection_state = None
        self._cached_projection = None
        self._cached_projection_metrics = None
        self._perf_callback = perf_callback
        self._perf_threshold = 0.05
        self._last_gcode_lines = None
        self._full_parse_skipped = False
        self._streaming_mode = False
        self._streaming_prev_render_interval = None
        self._deferred_full_parse = False

    def _legend_label(self, parent, color, text, var):
        swatch = tk.Label(parent, width=2, background=color)
        swatch.pack(side="left", padx=(0, 4), pady=(2, 2))
        chk = ttk.Checkbutton(parent, text=text, variable=var, command=self._schedule_render)
        chk.pack(side="left", padx=(0, 10))

    def set_performance_controls(
        self,
        perf_var,
        perf_value_var,
        on_move: Callable[[str], Any] | None = None,
        on_commit: Callable[[tk.Event], Any] | None = None,
        on_key_release: Callable[[tk.Event], Any] | None = None,
    ):
        if self._perf_frame is None:
            frame = ttk.Frame(self._legend_frame)
            frame.pack(side="left", padx=(8, 0))
            ttk.Label(frame, text="3D Performance").pack(side="left")
            ttk.Label(frame, text="Min").pack(side="left", padx=(6, 2))
            scale_kwargs: dict[str, Any] = {
                "master": frame,
                "from_": 0,
                "to": 100,
                "orient": "horizontal",
                "length": 140,
                "variable": perf_var,
            }
            if on_move:
                scale_kwargs["command"] = on_move
            scale = ttk.Scale(**scale_kwargs)
            scale.pack(side="left", padx=(4, 4))
            ttk.Label(frame, text="Max").pack(side="left", padx=(2, 6))
            if on_commit:
                scale.bind("<ButtonRelease-1>", on_commit)
            if on_key_release:
                scale.bind("<KeyRelease>", on_key_release)
            value_label = ttk.Label(frame, textvariable=perf_value_var, width=4)
            value_label.pack(side="left")
            apply_tooltip(scale, "Adjust 3D preview quality vs speed.")
            self._perf_frame = frame
            self._perf_scale = scale
            self._perf_value_label = value_label
        else:
            if self._perf_scale is not None:
                scale_kwargs = {"variable": perf_var}
                if on_move:
                    scale_kwargs["command"] = on_move
                self._perf_scale.configure(**scale_kwargs)
            if self._perf_value_label is not None:
                self._perf_value_label.configure(textvariable=perf_value_var)

    def _report_perf(self, label: str, duration: float):
        if not self._perf_callback:
            return
        if duration < self._perf_threshold:
            return
        try:
            self._perf_callback(label, duration)
        except Exception:
            pass

    def _invalidate_render_cache(self):
        self._cached_projection_state = None
        self._cached_projection = None
        self._cached_projection_metrics = None

    def _clamp_draw_percent(self, value) -> int:
        try:
            percent = int(round(float(value)))
        except Exception:
            percent = self._draw_percent_default
        return max(0, min(100, percent))

    def _apply_draw_percent(self, percent: int, update_scale: bool):
        if percent == self._draw_percent:
            return
        self._draw_percent = percent
        self._draw_percent_text.set(f"{percent}%")
        if update_scale:
            scale = getattr(self, "draw_percent_scale", None)
            if scale is not None:
                try:
                    scale.set(percent)
                except Exception:
                    pass
        self._invalidate_render_cache()
        self._schedule_render()
        if (
            percent >= 100
            and self._full_parse_skipped
            and self._last_gcode_lines
            and len(self._last_gcode_lines) > self._full_parse_limit
        ):
            if self._streaming_mode:
                self._deferred_full_parse = True
            else:
                self.set_gcode_async(self._last_gcode_lines)

    def _draw_target(self, total_segments: int, max_draw: int | None) -> int:
        if total_segments <= 0:
            return 0
        if self._draw_percent <= 0:
            return 0
        if self._draw_percent >= 100:
            return total_segments
        target = int(round(total_segments * (self._draw_percent / 100.0)))
        if target <= 0:
            target = 1
        if max_draw:
            target = min(target, max_draw)
        return min(target, total_segments)

    def _sample_segments(self, segments, target: int):
        total_segments = len(segments)
        if target <= 0 or total_segments <= 0:
            return []
        if target >= total_segments:
            return segments
        step = total_segments / float(target)
        sampled = []
        pos = 0.0
        for _ in range(target):
            idx = int(pos)
            if idx >= total_segments:
                idx = total_segments - 1
            sampled.append(segments[idx])
            pos += step
        return sampled

    def _build_projection_cache(self, filters: tuple[bool, bool, bool], max_draw: int | None):
        start = time.perf_counter()
        try:
            segments = self.segments
            total_segments = len(segments)
            target = self._draw_target(total_segments, max_draw)
            if target <= 0:
                return [], None, 0, total_segments
            draw_segments = self._sample_segments(segments, target)
            proj: list[tuple[float, float, float, float, str]] = []
            minx = miny = float("inf")
            maxx = maxy = float("-inf")
            drawn = 0
            for x1, y1, z1, x2, y2, z2, color in draw_segments:
                if color == "rapid" and not filters[0]:
                    continue
                if color == "feed" and not filters[1]:
                    continue
                if color == "arc" and not filters[2]:
                    continue
                px1, py1 = self._project(x1, y1, z1)
                px2, py2 = self._project(x2, y2, z2)
                minx = min(minx, px1, px2)
                miny = min(miny, py1, py2)
                maxx = max(maxx, px1, px2)
                maxy = max(maxy, py1, py2)
                proj.append((px1, py1, px2, py2, color))
                drawn += 1
            bounds = None
            if proj and (minx < float("inf")):
                bounds = (minx, maxx, miny, maxy)
            return proj, bounds, drawn, total_segments
        finally:
            self._report_perf("build_projection", time.perf_counter() - start)

    def set_display_options(
        self,
        rapid: bool | None = None,
        feed: bool | None = None,
        arc: bool | None = None,
    ):
        changed = False
        if rapid is not None:
            self.show_rapid.set(bool(rapid))
            changed = True
        if feed is not None:
            self.show_feed.set(bool(feed))
            changed = True
        if arc is not None:
            self.show_arc.set(bool(arc))
            changed = True
        if changed:
            self._schedule_render()
            self._invalidate_render_cache()

    def get_display_options(self) -> tuple[bool, bool, bool]:
        return (
            bool(self.show_rapid.get()),
            bool(self.show_feed.get()),
            bool(self.show_arc.get()),
        )

    def set_gcode(self, lines: list[str]):
        segs, bnds = self._parse_gcode(lines)
        if segs is not None:
            self.segments, self.bounds = segs, bnds
            self._invalidate_render_cache()
        self._schedule_render()

    def select_arc_step_rad(self, line_count: int) -> float:
        if line_count > self._full_parse_limit:
            base_step = self._arc_step_large
        elif line_count > 5000:
            base_step = self._arc_step_fast
        else:
            base_step = self._arc_step_default
        if self._arc_step_override_rad is not None:
            return self._arc_step_override_rad
        return base_step

    def set_gcode_async(self, lines: list[str], *, lines_hash: str | None = None):
        self._parse_token += 1
        token = self._parse_token
        self._last_gcode_lines = lines
        lines_hash = lines_hash if lines_hash is not None else _hash_lines(lines)
        if (
            lines_hash
            and (lines_hash == self._last_lines_hash)
            and self._last_segments is not None
            and not self._full_parse_skipped
        ):
            self.segments = self._last_segments
            self.bounds = self._last_bounds
            self._schedule_render()
            return
        line_count = len(lines)
        self._arc_step_rad = self.select_arc_step_rad(line_count)
        if not self.enabled:
            self._pending_parsed = None
            self._pending_lines = lines
            return
        self._pending_lines = None
        if not lines:
            self._pending_parsed = None
            self.segments = []
            self.bounds = None
            self._full_parse_skipped = False
            self._schedule_render()
            return
        preview_target = (
            self._lightweight_preview_target
            if (self._lightweight_mode or self._streaming_mode)
            else self._preview_target
        )
        quick_lines = lines
        if len(lines) > preview_target:
            step = max(2, len(lines) // preview_target)
            quick_lines = lines[::step]
        res = self._parse_gcode(quick_lines, token)
        if res[0] is None:
            return
        self.segments, self.bounds = res
        if quick_lines is lines:
            self._cache_parse_results(lines_hash, self.segments, self.bounds)
        self._schedule_render()
        if len(lines) > self._full_parse_limit:
            allow_full_parse = self._draw_percent >= 100 or self._max_draw_segments is None
            if self._streaming_mode:
                allow_full_parse = False
            if not allow_full_parse:
                self._full_parse_skipped = True
                return
        if self._streaming_mode:
            self._full_parse_skipped = quick_lines is not lines
            return
        self._full_parse_skipped = False
        def worker():
            segs, bnds = self._parse_gcode(lines, token)
            if segs is None:
                return
            if not self.winfo_exists():
                return
            root = self.winfo_toplevel()
            if getattr(root, "_closing", False):
                return
            self.after(0, lambda: self._apply_full_parse(token, segs, bnds, lines_hash))

        threading.Thread(target=worker, daemon=True).start()

    def apply_parsed_gcode(
        self,
        lines: list[str],
        segments,
        bounds,
        *,
        lines_hash: str | None = None,
    ):
        self._parse_token += 1
        self._last_gcode_lines = lines
        line_count = len(lines)
        self._arc_step_rad = self.select_arc_step_rad(line_count)
        lines_hash = lines_hash if lines_hash is not None else _hash_lines(lines)
        segments = segments or []
        self._cache_parse_results(lines_hash, segments, bounds)
        self._full_parse_skipped = False
        self._pending_lines = None
        if not self.enabled:
            self._pending_parsed = (segments, bounds, lines_hash)
            self.segments = []
            self.bounds = None
            return
        self._pending_parsed = None
        self.segments = segments
        self.bounds = bounds
        self._invalidate_render_cache()
        self._schedule_render()

    def _cache_parse_results(self, lines_hash: str | None, segments, bounds):
        if not lines_hash:
            return
        self._last_lines_hash = lines_hash
        self._last_segments = segments
        self._last_bounds = bounds

    def set_lightweight_mode(self, lightweight: bool):
        new_mode = bool(lightweight)
        if self._lightweight_mode == new_mode:
            return
        self._lightweight_mode = new_mode
        self._schedule_render()

    def set_job_name(self, name: str | None):
        self._job_name = str(name) if name else ""
        self._schedule_render()


    def _apply_full_parse(self, token, segments, bounds, parse_hash: str | None = None):
        if not self.winfo_exists():
            return
        root = self.winfo_toplevel()
        if getattr(root, "_closing", False):
            return
        if token != self._parse_token:
            return
        if not self.enabled:
            self._pending_lines = None
            return
        self._full_parse_skipped = False
        self._cache_parse_results(parse_hash, segments, bounds)
        self.segments = segments
        self.bounds = bounds
        self._invalidate_render_cache()
        self._schedule_render()

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.segments = []
            self.bounds = None
            self._schedule_render()
            return
        if self._pending_parsed is not None:
            segments, bounds, lines_hash = self._pending_parsed
            self._pending_parsed = None
            self._cache_parse_results(lines_hash, segments, bounds)
            self.segments = segments
            self.bounds = bounds
            self._full_parse_skipped = False
            self._invalidate_render_cache()
            self._schedule_render()
            return
        if self._pending_lines is not None:
            pending = self._pending_lines
            self._pending_lines = None
            self.set_gcode_async(pending)

    def set_visible(self, visible: bool):
        self._visible = bool(visible)
        if self._visible:
            self._schedule_render()

    def set_position(self, x: float, y: float, z: float):
        self.position = (x, y, z)
        if self._visible and self.enabled:
            if not self.segments:
                return
            if self._render_params and not self._render_pending:
                self._update_position_marker()
            else:
                self._schedule_render()

    def _update_position_marker(self):
        if not self._render_params:
            return
        if not self.position:
            if self._position_item is not None:
                try:
                    self.canvas.delete(self._position_item)
                except Exception:
                    pass
                self._position_item = None
            return
        params = self._render_params
        px, py = self._project(*self.position)
        cx = (px - params["minx"]) * params["scale"] + params["margin"]
        cy = (py - params["miny"]) * params["scale"] + params["margin"]
        cx = cx + params["pan_x"]
        cy = params["height"] - cy + params["pan_y"]
        r = 4
        if self._position_item is None:
            self._position_item = self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill="#d64545", outline=""
            )
        else:
            self.canvas.coords(self._position_item, cx - r, cy - r, cx + r, cy + r)

    def _on_resize(self, _event=None):
        self._schedule_render()

    def _on_drag_start(self, event):
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event):
        if not self._drag_start:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self._drag_start = (event.x, event.y)
        self.azimuth += dx * 0.01
        self.elevation += dy * 0.01
        limit = math.pi / 2 - 0.1
        self.elevation = max(-limit, min(limit, self.elevation))
        self._schedule_render()
        self._enter_fast_mode()

    def _on_pan_start(self, event):
        self._pan_start = (event.x, event.y)

    def _on_pan(self, event):
        if not self._pan_start:
            return
        dx = event.x - self._pan_start[0]
        dy = event.y - self._pan_start[1]
        self._pan_start = (event.x, event.y)
        self.pan_x += dx
        self.pan_y += dy
        self._schedule_render()
        self._enter_fast_mode()

    def _on_mousewheel(self, event):
        if hasattr(event, "delta") and event.delta:
            direction = 1 if event.delta > 0 else -1
        else:
            direction = 1 if event.num == 4 else -1
        if direction > 0:
            self.zoom *= 1.1
        else:
            self.zoom /= 1.1
        self.zoom = max(0.2, min(5.0, self.zoom))
        self._schedule_render()
        self._enter_fast_mode()

    def _reset_view(self):
        self.azimuth = math.radians(45)
        self.elevation = math.radians(30)
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._schedule_render()

    def _save_view(self):
        if callable(self.on_save_view):
            self.on_save_view()

    def _load_view(self):
        if callable(self.on_load_view):
            self.on_load_view()

    def get_view(self) -> dict:
        return {
            "azimuth": self.azimuth,
            "elevation": self.elevation,
            "zoom": self.zoom,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
        }

    def apply_view(self, view: dict):
        if not view:
            return
        try:
            self.azimuth = float(view.get("azimuth", self.azimuth))
            self.elevation = float(view.get("elevation", self.elevation))
            self.zoom = float(view.get("zoom", self.zoom))
            self.pan_x = float(view.get("pan_x", self.pan_x))
            self.pan_y = float(view.get("pan_y", self.pan_y))
        except Exception as exc:
            logger.exception("Failed to apply 3D view state: %s", exc)
            return
        self._schedule_render()

    def _schedule_render(self):
        if not self._visible:
            return
        if self._render_pending:
            return
        self._render_pending = True
        now = time.time()
        delay = max(0.0, self._render_interval - (now - self._last_render_ts))
        self.after(int(delay * 1000), self._render)

    def set_draw_limits(self, full_limit: int | None = None, interactive_limit: int | None = None):
        if full_limit is not None:
            if full_limit <= 0:
                self._max_draw_segments = None
            else:
                self._max_draw_segments = int(full_limit)
        if interactive_limit is not None:
            if interactive_limit <= 0:
                self._interactive_max_draw_segments = None
            else:
                self._interactive_max_draw_segments = int(interactive_limit)
        self._invalidate_render_cache()
        self._schedule_render()

    def set_draw_percent(self, percent):
        percent = self._clamp_draw_percent(percent)
        self._apply_draw_percent(percent, update_scale=True)

    def get_draw_percent(self) -> int:
        return int(self._draw_percent)

    def set_arc_detail_override(self, step_rad: float | None):
        if step_rad is None or step_rad <= 0:
            self._arc_step_override_rad = None
        else:
            self._arc_step_override_rad = float(step_rad)
        self._schedule_render()

    def set_streaming_render_interval(self, interval: float):
        try:
            value = float(interval)
        except Exception:
            return
        value = max(0.05, min(2.0, value))
        self._streaming_render_interval = value
        if self._streaming_mode:
            base_interval = (
                self._streaming_prev_render_interval
                if self._streaming_prev_render_interval is not None
                else self._render_interval
            )
            self._render_interval = max(base_interval, self._streaming_render_interval)
            self._schedule_render()

    def set_streaming_mode(self, streaming: bool):
        streaming = bool(streaming)
        if self._streaming_mode == streaming:
            return
        self._streaming_mode = streaming
        if streaming:
            self._streaming_prev_render_interval = self._render_interval
            self._render_interval = max(self._render_interval, self._streaming_render_interval)
        else:
            if self._streaming_prev_render_interval is not None:
                self._render_interval = self._streaming_prev_render_interval
            if self._deferred_full_parse and self._last_gcode_lines:
                self._deferred_full_parse = False
                self.set_gcode_async(self._last_gcode_lines)
        self._schedule_render()

    def _on_draw_percent_slider(self, value):
        percent = self._clamp_draw_percent(value)
        self._apply_draw_percent(percent, update_scale=False)

    def _enter_fast_mode(self):
        self._fast_mode = True
        if self._fast_mode_after_id is not None:
            try:
                self.after_cancel(self._fast_mode_after_id)
            except Exception:
                pass
        self._fast_mode_after_id = self.after(int(self._fast_mode_duration * 1000), self._exit_fast_mode)

    def _exit_fast_mode(self):
        self._fast_mode_after_id = None
        if not self._fast_mode:
            return
        self._fast_mode = False
        self._schedule_render()

    def _project(self, x: float, y: float, z: float) -> tuple[float, float]:
        ca = math.cos(self.azimuth)
        sa = math.sin(self.azimuth)
        ce = math.cos(self.elevation)
        se = math.sin(self.elevation)
        x1 = x * ca - y * sa
        y1 = x * sa + y * ca
        y2 = y1 * ce - z * se
        return x1, y2

    def _segments_bounds(self, segments):
        if not segments:
            return None
        minx = miny = minz = float("inf")
        maxx = maxy = maxz = float("-inf")
        for x1, y1, z1, x2, y2, z2, _ in segments:
            minx = min(minx, x1, x2)
            miny = min(miny, y1, y2)
            minz = min(minz, z1, z2)
            maxx = max(maxx, x1, x2)
            maxy = max(maxy, y1, y2)
            maxz = max(maxz, z1, z2)
        return minx, maxx, miny, maxy, minz, maxz

    def _parse_gcode(self, lines: list[str], token: int | None = None):
        start = time.perf_counter()
        try:
            def keep_running() -> bool:
                return token is None or token == self._parse_token

            result = parse_gcode_lines(lines, self._arc_step_rad, keep_running=keep_running)
            if result is None:
                return None, None
            return result.segments, result.bounds
        finally:
            self._report_perf("parse_gcode", time.perf_counter() - start)

    def _render(self):
        self._render_pending = False
        if not self._visible:
            return
        self._last_render_ts = time.time()
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.canvas.delete("all")
        self._position_item = None
        self._render_params = None
        if not self.enabled:
            job_txt = f" (Job: {self._job_name})" if self._job_name else ""
            self.canvas.create_text(
                w / 2,
                h / 2 - 10,
                text=f"3D render disabled{job_txt}",
                fill="#666666",
            )
            if self._job_name:
                self.canvas.create_text(
                    w / 2,
                    h / 2 + 10,
                    text="Enable 3D render for the full preview.",
                    fill="#666666",
                )
            return
        if not self.segments:
            self.canvas.create_text(
                w / 2,
                h / 2 - 10,
                text="No G-code loaded",
                fill="#666666",
            )
            if self._job_name:
                self.canvas.create_text(
                    w / 2,
                    h / 2 + 10,
                    text=f"Last job: {self._job_name}",
                    fill="#666666",
                )
            return

        total_segments = len(self.segments)
        segments = self.segments
        max_draw = self._max_draw_segments
        if self._fast_mode and self._interactive_max_draw_segments:
            if max_draw:
                max_draw = min(max_draw, self._interactive_max_draw_segments)
            else:
                max_draw = self._interactive_max_draw_segments
        if self._streaming_mode and self._interactive_max_draw_segments:
            if max_draw:
                max_draw = min(max_draw, self._interactive_max_draw_segments)
            else:
                max_draw = self._interactive_max_draw_segments
        target = self._draw_target(total_segments, max_draw)
        if target <= 0:
            self.canvas.create_text(
                w / 2,
                h / 2 - 10,
                text="Draw percent set to 0%",
                fill="#666666",
            )
            if self._job_name:
                self.canvas.create_text(
                    w / 2,
                    h / 2 + 10,
                    text=f"Last job: {self._job_name}",
                    fill="#666666",
                )
            return
        if target < total_segments:
            segments = self._sample_segments(segments, target)
        proj = []
        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        for x1, y1, z1, x2, y2, z2, color in segments:
            if color == "rapid" and not self.show_rapid.get():
                continue
            if color == "feed" and not self.show_feed.get():
                continue
            if color == "arc" and not self.show_arc.get():
                continue
            px1, py1 = self._project(x1, y1, z1)
            px2, py2 = self._project(x2, y2, z2)
            minx = min(minx, px1, px2)
            miny = min(miny, py1, py2)
            maxx = max(maxx, px1, px2)
            maxy = max(maxy, py1, py2)
            proj.append((px1, py1, px2, py2, color))

        if not proj:
            self.canvas.create_text(w / 2, h / 2, text="No toolpath selected", fill="#666666")
            return

        if maxx - minx == 0 or maxy - miny == 0:
            return
        margin = 20
        sx = (w - 2 * margin) / (maxx - minx)
        sy = (h - 2 * margin) / (maxy - miny)
        scale = min(sx, sy) * self.zoom

        def to_canvas(px, py):
            cx = (px - minx) * scale + margin
            cy = (py - miny) * scale + margin
            return cx + self.pan_x, h - cy + self.pan_y

        self._render_params = {
            "minx": minx,
            "miny": miny,
            "scale": scale,
            "margin": margin,
            "height": h,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
        }

        runs: dict[str, list[list[float]]] = {}
        cur_color = None
        cur_pts: list[float] = []
        last_end = None

        def flush_run():
            nonlocal cur_color, cur_pts, last_end
            if cur_color and len(cur_pts) >= 4:
                runs.setdefault(cur_color, []).append(cur_pts)
            cur_color = None
            cur_pts = []
            last_end = None

        eps = 1e-6
        for px1, py1, px2, py2, color in proj:
            color_hex = self._colors.get(color, "#2c6dd2")
            x1, y1 = to_canvas(px1, py1)
            x2, y2 = to_canvas(px2, py2)
            continuous = (
                cur_color == color
                and last_end is not None
                and abs(x1 - last_end[0]) <= eps
                and abs(y1 - last_end[1]) <= eps
            )
            if not continuous:
                flush_run()
                cur_color = color
                cur_pts = [x1, y1, x2, y2]
            else:
                cur_pts.extend([x2, y2])
            last_end = (x2, y2)
        flush_run()

        for color, polylines in runs.items():
            color_hex = self._colors.get(color, "#2c6dd2")
            for pts in polylines:
                self.canvas.create_line(*pts, fill=color_hex)

        x0, y0 = to_canvas(minx, miny)
        x1, y1 = to_canvas(maxx, maxy)
        x_low, x_high = min(x0, x1), max(x0, x1)
        y_low, y_high = min(y0, y1), max(y0, y1)
        self.canvas.create_rectangle(
            x_low,
            y_low,
            x_high,
            y_high,
            outline="#ffffff",
            width=1,
        )

        origin = self._project(0.0, 0.0, 0.0)
        ox, oy = to_canvas(*origin)
        cross = 6
        self.canvas.create_line(ox - cross, oy, ox + cross, oy, fill="#ffffff")
        self.canvas.create_line(ox, oy - cross, ox, oy + cross, fill="#ffffff")

        drawn = len(proj)
        filters = []
        if self.show_rapid.get():
            filters.append("Rapid")
        if self.show_feed.get():
            filters.append("Feed")
        if self.show_arc.get():
            filters.append("Arc")
        filters_text = ", ".join(filters) if filters else "None"
        az_deg = math.degrees(self.azimuth)
        el_deg = math.degrees(self.elevation)
        mode_text = "Fast preview" if self._fast_mode else "Full quality"
        overlay = "\n".join(
            [
                f"Segments: {drawn:,}/{total_segments:,}",
                f"Draw: {self._draw_percent}%",
                f"View: Az {az_deg:.0f}° El {el_deg:.0f}° Zoom {self.zoom:.2f}x",
                f"Filters: {filters_text}",
                f"Mode: {mode_text}",
            ]
        )
        self.canvas.create_text(
            margin + 6,
            margin + 6,
            text=overlay,
            fill="#ffffff",
            anchor="nw",
            justify="left",
        )

        self._update_position_marker()


class TopViewPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=_resolve_widget_bg(self), highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self._schedule_render())
        self.segments: list[tuple[float, float, float, float, float, float, str]] = []
        self.bounds: tuple[float, float, float, float, float, float] | None = None
        self._job_name = ""
        self._visible = True
        self._render_pending = False
        self._parse_token = 0
        self._arc_step_rad = math.pi / 18
        self._colors = _TOOLPATH_SEGMENT_COLORS
        self._last_lines_hash = None

    def set_lines(self, lines: list[str] | None):
        self._parse_token += 1
        token = self._parse_token
        self._last_lines_hash = None
        if not lines:
            self.segments = []
            self.bounds = None
            self._schedule_render()
            return
        def worker(parse_lines=lines, parse_token=token):
            result = parse_gcode_lines(parse_lines, self._arc_step_rad)
            if result is None:
                return
            self.after(0, lambda res=result, tok=parse_token: self._apply_parse_result(tok, res))

        threading.Thread(target=worker, daemon=True).start()

    def apply_parsed_gcode(self, segments, bounds, *, lines_hash: str | None = None):
        if lines_hash is not None and lines_hash == self._last_lines_hash:
            self.segments = segments or []
            self.bounds = bounds
            self._schedule_render()
            return
        self._parse_token += 1
        self._last_lines_hash = lines_hash
        self.segments = segments or []
        self.bounds = bounds
        self._schedule_render()

    def _apply_parse_result(self, token: int, result):
        if token != self._parse_token or result is None:
            return
        self.segments = result.segments
        self.bounds = result.bounds
        self._schedule_render()

    def clear(self):
        self._parse_token += 1
        self.segments = []
        self.bounds = None
        self._job_name = ""
        self._last_lines_hash = None
        self._schedule_render()

    def set_job_name(self, name: str | None):
        self._job_name = str(name) if name else ""
        self._schedule_render()

    def set_visible(self, visible: bool):
        visible = bool(visible)
        if self._visible == visible:
            return
        self._visible = visible
        if self._visible:
            self._schedule_render()

    def _segments_bounds(self, segments):
        if not segments:
            return None
        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        for x1, y1, _, x2, y2, _, _ in segments:
            minx = min(minx, x1, x2)
            miny = min(miny, y1, y2)
            maxx = max(maxx, x1, x2)
            maxy = max(maxy, y1, y2)
        return minx, maxx, miny, maxy, 0.0, 0.0

    def _schedule_render(self):
        if not self._visible or self._render_pending:
            return
        self._render_pending = True
        self.after_idle(self._render)

    def _render(self):
        self._render_pending = False
        if not self.winfo_exists():
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.canvas.delete("all")
        if not self.segments:
            msg = "No G-code loaded"
            if self._job_name:
                msg = f"{self._job_name}\n{msg}"
            self.canvas.create_text(w / 2, h / 2, text=msg, fill="#666666", justify="center")
            return
        bounds = self.bounds or self._segments_bounds(self.segments)
        if not bounds:
            return
        minx, maxx, miny, maxy, _, _ = bounds
        dx = max(maxx - minx, 1e-6)
        dy = max(maxy - miny, 1e-6)
        margin = 20
        scale_x = max(w - margin * 2, 1) / dx
        scale_y = max(h - margin * 2, 1) / dy
        scale = min(scale_x, scale_y)
        offset_x = (w - dx * scale) / 2
        offset_y = (h - dy * scale) / 2

        def to_canvas(x, y):
            cx = (x - minx) * scale + offset_x
            cy = h - ((y - miny) * scale + offset_y)
            return cx, cy

        runs: dict[str, list[list[float]]] = {}
        cur_color = None
        cur_pts: list[float] = []
        last_end = None

        def flush_run():
            nonlocal cur_color, cur_pts, last_end
            if cur_color and len(cur_pts) >= 4:
                runs.setdefault(cur_color, []).append(cur_pts)
            cur_color = None
            cur_pts = []
            last_end = None

        eps = 1e-6
        for x1, y1, _, x2, y2, _, color in self.segments:
            px1, py1 = to_canvas(x1, y1)
            px2, py2 = to_canvas(x2, y2)
            continuous = (
                cur_color == color
                and last_end is not None
                and abs(px1 - last_end[0]) <= eps
                and abs(py1 - last_end[1]) <= eps
            )
            if not continuous:
                flush_run()
                cur_color = color
                cur_pts = [px1, py1, px2, py2]
            else:
                cur_pts.extend([px2, py2])
            last_end = (px2, py2)
        flush_run()

        for color, polylines in runs.items():
            color_hex = self._colors.get(color, "#2c6dd2")
            for pts in polylines:
                self.canvas.create_line(*pts, fill=color_hex)

        x0, y0 = to_canvas(minx, miny)
        x1, y1 = to_canvas(maxx, maxy)
        self.canvas.create_rectangle(
            min(x0, x1),
            min(y0, y1),
            max(x0, x1),
            max(y0, y1),
            outline="#ffffff",
            width=1,
        )

        if minx <= 0 <= maxx and miny <= 0 <= maxy:
            ox, oy = to_canvas(0.0, 0.0)
            cross = 6
            self.canvas.create_line(ox - cross, oy, ox + cross, oy, fill="#ffffff")
            self.canvas.create_line(ox, oy - cross, ox, oy + cross, fill="#ffffff")

        overlay = [f"Segments: {len(self.segments):,}", "View: Top"]
        if self._job_name:
            overlay.insert(0, f"Job: {self._job_name}")
        self.canvas.create_text(
            12,
            12,
            text="\n".join(overlay),
            fill="#ffffff",
            anchor="nw",
            justify="left",
        )


