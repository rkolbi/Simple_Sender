import queue

import pytest

pytest.importorskip("tkinter")
import tkinter as tk
from tkinter import ttk

from simple_sender.ui.settings import sections_general

pytestmark = pytest.mark.ui


def test_build_power_section_is_noop_on_non_linux(tk_root, monkeypatch) -> None:
    parent = ttk.Frame(tk_root)
    monkeypatch.setattr(sections_general.sys, "platform", "win32")

    row = sections_general.build_power_section(object(), parent, row=4)

    assert row == 4


def test_power_section_falls_back_to_shutdown_command_when_systemctl_fails(tk_root, monkeypatch) -> None:
    parent = ttk.Frame(tk_root)

    class _Status:
        def __init__(self) -> None:
            self.text = ""

        def config(self, *, text: str) -> None:
            self.text = text

    class _App:
        def __init__(self) -> None:
            self.ui_q: queue.Queue[tuple[str, str]] = queue.Queue()
            self.status = _Status()
            self.save_calls = 0

        def _save_settings(self) -> None:
            self.save_calls += 1

    app = _App()
    monkeypatch.setattr(sections_general.sys, "platform", "linux")
    monkeypatch.setattr(sections_general.messagebox, "askyesno", lambda *_args, **_kwargs: True)

    popen_calls: list[list[str]] = []

    def _fake_popen(args):
        popen_calls.append(list(args))
        if args[:1] == ["systemctl"]:
            raise OSError("systemctl unavailable")
        return object()

    monkeypatch.setattr(sections_general.subprocess, "Popen", _fake_popen)

    row = sections_general.build_power_section(app, parent, row=1)
    app.btn_shutdown.invoke()

    assert row == 2
    assert app.save_calls == 1
    assert popen_calls == [["systemctl", "poweroff"], ["shutdown", "-h", "now"]]
    assert app.status.text == "[system] Shutdown requested"
    assert app.ui_q.get_nowait() == ("log", "[system] Shutdown requested")


def test_power_section_pi_profile_applies_and_saves(tk_root, monkeypatch) -> None:
    parent = ttk.Frame(tk_root)
    monkeypatch.setattr(sections_general.sys, "platform", "linux")

    class _Status:
        def __init__(self) -> None:
            self.text = ""

        def config(self, *, text: str) -> None:
            self.text = text

    class _ToolpathPanel:
        def __init__(self) -> None:
            self.enabled_values: list[bool] = []

        def set_enabled(self, value: bool) -> None:
            self.enabled_values.append(bool(value))

    class _App:
        def __init__(self) -> None:
            self.ui_q: queue.Queue[tuple[str, str]] = queue.Queue()
            self.status = _Status()
            self.toolpath_panel = _ToolpathPanel()
            self.pi_profile_enabled = tk.BooleanVar(master=tk_root, value=False)
            self.performance_mode = tk.BooleanVar(master=tk_root, value=False)
            self.gui_logging_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.validate_streaming_gcode = tk.BooleanVar(master=tk_root, value=True)
            self.streaming_line_threshold = tk.IntVar(master=tk_root, value=250000)
            self.status_poll_interval = tk.DoubleVar(master=tk_root, value=0.2)
            self.toolpath_lightweight = tk.BooleanVar(master=tk_root, value=False)
            self.toolpath_streaming_render_interval = tk.DoubleVar(master=tk_root, value=0.25)
            self.render3d_enabled = tk.BooleanVar(master=tk_root, value=True)
            self.show_autolevel_overlay = tk.BooleanVar(master=tk_root, value=True)
            self.save_calls = 0
            self.calls: dict[str, int] = {}

        def _save_settings(self) -> None:
            self.save_calls += 1

        def _mark(self, key: str) -> None:
            self.calls[key] = self.calls.get(key, 0) + 1

        def _on_performance_mode_change(self) -> None:
            self._mark("perf")

        def _on_gui_logging_change(self) -> None:
            self._mark("gui_log")

        def _on_status_interval_change(self) -> None:
            self._mark("status")

        def _on_toolpath_lightweight_change(self) -> None:
            self._mark("lightweight")

        def _apply_toolpath_streaming_render_interval(self, _event=None) -> None:
            self._mark("stream_interval")

        def _refresh_render_3d_toggle_text(self) -> None:
            self._mark("render_text")

        def _on_autolevel_overlay_change(self) -> None:
            self._mark("overlay")

    app = _App()

    row = sections_general.build_power_section(app, parent, row=2)
    app.pi_profile_check.invoke()

    assert row == 3
    assert app.pi_profile_enabled.get() is True
    assert app.performance_mode.get() is True
    assert app.gui_logging_enabled.get() is False
    assert app.validate_streaming_gcode.get() is False
    assert app.streaming_line_threshold.get() == sections_general.PI_PROFILE_STREAMING_LINE_THRESHOLD
    assert app.status_poll_interval.get() == sections_general.PI_PROFILE_STATUS_POLL_INTERVAL
    assert app.toolpath_lightweight.get() is True
    assert (
        app.toolpath_streaming_render_interval.get()
        == sections_general.PI_PROFILE_STREAMING_RENDER_INTERVAL
    )
    assert app.render3d_enabled.get() is False
    assert app.show_autolevel_overlay.get() is False
    assert app.toolpath_panel.enabled_values == [False]
    assert app.calls["perf"] == 1
    assert app.calls["gui_log"] == 1
    assert app.calls["status"] == 1
    assert app.calls["lightweight"] == 1
    assert app.calls["stream_interval"] == 1
    assert app.calls["render_text"] == 1
    assert app.calls["overlay"] == 1
    assert app.save_calls == 1
    assert app.status.text == "[settings] Pi profile enabled"
    assert app.ui_q.get_nowait() == ("log", "[settings] Pi profile enabled")
