import pytest

tk = pytest.importorskip("tkinter")

from simple_sender.ui.toolpath import toolpath_settings


class _Panel:
    def __init__(self) -> None:
        self.calls = []

    def set_draw_limits(self, full, interactive) -> None:
        self.calls.append(("limits", full, interactive))

    def set_arc_detail(self, value: float) -> None:
        self.calls.append(("arc", value))

    def set_lightweight(self, value: bool) -> None:
        self.calls.append(("lightweight", value))

    def set_draw_percent(self, value: int) -> None:
        self.calls.append(("draw", value))

    def reparse_lines(self, lines, lines_hash=None) -> None:
        self.calls.append(("reparse", list(lines), lines_hash))

    def set_gcode_lines(self, lines, lines_hash=None) -> None:
        self.calls.append(("set_gcode", list(lines), lines_hash))

    def set_streaming_render_interval(self, interval: float) -> None:
        self.calls.append(("stream_interval", interval))

    def set_enabled(self, value: bool) -> None:
        self.calls.append(("enabled", value))

    def get_view_state(self):
        return {"zoom": 1.0}

    def apply_view_state(self, view) -> None:
        self.calls.append(("view", view))


class _Status:
    def __init__(self) -> None:
        self.text = None

    def config(self, text: str) -> None:
        self.text = text


class _App:
    def __init__(self, root, settings=None) -> None:
        self.settings = settings or {}
        self.toolpath_panel = _Panel()
        self.render3d_enabled = tk.BooleanVar(master=root, value=False)
        self._last_gcode_lines = []
        self._gcode_hash = "hash"
        self._stream_state = "idle"
        self._toolpath_reparse_deferred = False
        self.status = _Status()
        self.refresh_called = False
        self._toolpath_arc_detail_reparse_after_id = None
        self._toolpath_arc_detail_reparse_delay = 300

        self._refresh_render_3d_toggle_text = lambda: setattr(
            self, "refresh_called", True
        )
        self._clamp_toolpath_performance = lambda value: toolpath_settings.clamp_toolpath_performance(
            self, value
        )
        self._toolpath_perf_values = lambda perf: toolpath_settings.toolpath_perf_values(
            self, perf
        )
        self._toolpath_limit_value = lambda raw, fallback: toolpath_settings.toolpath_limit_value(
            self, raw, fallback
        )
        self._clamp_toolpath_streaming_render_interval = (
            lambda value: toolpath_settings.clamp_toolpath_streaming_render_interval(
                self, value
            )
        )
        self._clamp_arc_detail = lambda value: toolpath_settings.clamp_arc_detail(self, value)
        self._apply_toolpath_performance = lambda: toolpath_settings.apply_toolpath_performance(
            self
        )
        self._apply_toolpath_arc_detail = lambda: toolpath_settings.apply_toolpath_arc_detail(
            self
        )
        self._schedule_toolpath_arc_detail_reparse = (
            lambda: toolpath_settings.schedule_toolpath_arc_detail_reparse(self)
        )
        self._run_toolpath_arc_detail_reparse = (
            lambda: toolpath_settings.run_toolpath_arc_detail_reparse(self)
        )

    def after(self, delay: int, func):
        self.after_called = (delay, func)
        return "after-id"

    def after_cancel(self, token: str) -> None:
        self.after_cancel_called = token


def _make_app(root, settings=None):
    app = _App(root, settings=settings)
    toolpath_settings.init_toolpath_settings(app)
    return app


def test_toolpath_limit_value_clamps_negative(tk_root) -> None:
    app = _make_app(tk_root)
    assert toolpath_settings.toolpath_limit_value(app, "-5", 10) == 0
    assert toolpath_settings.toolpath_limit_value(app, "bad", 12) == 12


def test_clamp_toolpath_performance_bounds(tk_root) -> None:
    app = _make_app(tk_root)
    assert toolpath_settings.clamp_toolpath_performance(app, -1) == 0.0
    assert toolpath_settings.clamp_toolpath_performance(app, 200) == 100.0
    assert toolpath_settings.clamp_toolpath_performance(app, "bad") == app._toolpath_performance_default


def test_toolpath_perf_values_full_quality(tk_root) -> None:
    app = _make_app(tk_root)
    full, interactive, arc, lightweight, draw = toolpath_settings.toolpath_perf_values(
        app, 100.0
    )
    assert full == 0
    assert interactive == 0
    assert arc == app._toolpath_arc_detail_min
    assert lightweight is False
    assert draw == 100


def test_apply_toolpath_streaming_render_interval_clamps(tk_root) -> None:
    app = _make_app(tk_root)
    app.toolpath_streaming_render_interval.set(10.0)

    toolpath_settings.apply_toolpath_streaming_render_interval(app)

    assert app.toolpath_streaming_render_interval.get() == 2.0
    assert ("stream_interval", 2.0) in app.toolpath_panel.calls


def test_apply_toolpath_performance_sets_deferred_when_streaming(tk_root) -> None:
    app = _make_app(tk_root)
    app._last_gcode_lines = ["G0 X0"]
    app._stream_state = "running"
    app.toolpath_performance.set(20.0)

    toolpath_settings.apply_toolpath_performance(app)

    assert app._toolpath_reparse_deferred is True
    assert not any(call[0] == "reparse" for call in app.toolpath_panel.calls)


def test_apply_toolpath_performance_reparses_when_idle(tk_root) -> None:
    app = _make_app(tk_root)
    app._last_gcode_lines = ["G0 X0"]
    app.toolpath_performance.set(30.0)

    toolpath_settings.apply_toolpath_performance(app)

    assert ("reparse", ["G0 X0"], "hash") in app.toolpath_panel.calls


def test_apply_toolpath_draw_limits_clamps_inputs(tk_root) -> None:
    app = _make_app(tk_root)
    app.toolpath_full_limit.set("-1")
    app.toolpath_interactive_limit.set("bad")

    toolpath_settings.apply_toolpath_draw_limits(app)

    assert app.toolpath_full_limit.get() == "0"
    assert app.toolpath_interactive_limit.get() == str(app._toolpath_interactive_limit_default)
    assert ("limits", 0, app._toolpath_interactive_limit_default) in app.toolpath_panel.calls


def test_apply_toolpath_arc_detail_schedules_reparse(tk_root) -> None:
    app = _make_app(tk_root)
    app.toolpath_arc_detail.set(10.0)

    toolpath_settings.apply_toolpath_arc_detail(app)

    assert app._toolpath_arc_detail_reparse_after_id == "after-id"
    assert app.after_called[0] == app._toolpath_arc_detail_reparse_delay
    assert ("arc", app.toolpath_arc_detail.get()) in app.toolpath_panel.calls


def test_schedule_toolpath_arc_detail_reparse_cancels_previous(tk_root) -> None:
    app = _make_app(tk_root)
    app._toolpath_arc_detail_reparse_after_id = "old-id"

    toolpath_settings.schedule_toolpath_arc_detail_reparse(app)

    assert app.after_cancel_called == "old-id"
    assert app._toolpath_arc_detail_reparse_after_id == "after-id"


def test_run_toolpath_arc_detail_reparse_defers_when_streaming(tk_root) -> None:
    app = _make_app(tk_root)
    app._last_gcode_lines = ["G0 X0"]
    app._stream_state = "paused"

    toolpath_settings.run_toolpath_arc_detail_reparse(app)

    assert app._toolpath_reparse_deferred is True
    assert not any(call[0] == "reparse" for call in app.toolpath_panel.calls)


def test_run_toolpath_arc_detail_reparse_runs_when_idle(tk_root) -> None:
    app = _make_app(tk_root)
    app._last_gcode_lines = ["G0 X0"]
    app._stream_state = "idle"

    toolpath_settings.run_toolpath_arc_detail_reparse(app)

    assert ("reparse", ["G0 X0"], "hash") in app.toolpath_panel.calls


def test_toggle_render_3d_enables_and_sets_lines(tk_root) -> None:
    app = _make_app(tk_root)
    app._last_gcode_lines = ["G1 X1"]

    toolpath_settings.toggle_render_3d(app)

    assert app.render3d_enabled.get() is True
    assert app.refresh_called is True
    assert ("enabled", True) in app.toolpath_panel.calls
    assert ("set_gcode", ["G1 X1"], "hash") in app.toolpath_panel.calls


def test_save_and_load_3d_view(tk_root) -> None:
    app = _make_app(tk_root)
    toolpath_settings.save_3d_view(app)
    assert app.settings["view_3d"] == {"zoom": 1.0}
    assert app.status.text == "3D view saved"

    toolpath_settings.load_3d_view(app)
    assert ("view", {"zoom": 1.0}) in app.toolpath_panel.calls
    assert app.status.text == "3D view loaded"
