import pytest

from simple_sender.ui.viewer import preview_policy
from simple_sender.utils.constants import GCODE_TOP_VIEW_STREAMING_SEGMENT_LIMIT

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _ToolpathPanel:
    def __init__(self):
        self.enabled = None
        self.cleared = False
        self.job_name = None
        self.top_view = None
        self.arc_steps = []

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def clear(self):
        self.cleared = True

    def set_job_name(self, name: str):
        self.job_name = name

    def get_arc_step_rad(self, line_count: int) -> float:
        self.arc_steps.append(line_count)
        return 0.25

    def set_top_view_lines(self, source, *, max_segments, arc_step_rad):
        self.top_view = (source, max_segments, arc_step_rad)


def test_set_preview_streaming_state_updates_flags() -> None:
    class _App:
        def __init__(self):
            self._gcode_streaming_mode = False
            self._render3d_blocked = False
            self.refresh_called = False

        def _refresh_render_3d_toggle_text(self):
            self.refresh_called = True

    app = _App()

    preview_policy.set_preview_streaming_state(app, True)

    assert app._gcode_streaming_mode is True
    assert app._render3d_blocked is True
    assert app.refresh_called is True


def test_configure_toolpath_preview_full_load_enables_3d() -> None:
    class _App:
        def __init__(self):
            self.render3d_enabled = _Var(True)
            self.toolpath_panel = _ToolpathPanel()

    app = _App()

    preview_policy.configure_toolpath_preview(app, "C:\\jobs\\demo.nc", ["G0 X0"], None)

    assert app.toolpath_panel.enabled is True
    assert app.toolpath_panel.cleared is True
    assert app.toolpath_panel.job_name == "demo.nc"
    assert app.toolpath_panel.top_view is None


def test_configure_toolpath_preview_streaming_sets_top_view() -> None:
    class _App:
        def __init__(self):
            self.render3d_enabled = _Var(True)
            self.toolpath_panel = _ToolpathPanel()
            self._gcode_total_lines = 42

    app = _App()
    source = ["G0 X0", "G0 X1"]

    preview_policy.configure_toolpath_preview(app, "job.nc", [], source, preview_only=True)

    assert app.toolpath_panel.enabled is False
    assert app.toolpath_panel.top_view[0] == source
    assert app.toolpath_panel.top_view[1] == GCODE_TOP_VIEW_STREAMING_SEGMENT_LIMIT
    assert app.toolpath_panel.top_view[2] == 0.25
    assert app.toolpath_panel.arc_steps == [42]


def test_configure_toolpath_preview_source_without_preview_only_keeps_3d() -> None:
    class _App:
        def __init__(self):
            self.render3d_enabled = _Var(True)
            self.toolpath_panel = _ToolpathPanel()
            self._gcode_total_lines = 100

    app = _App()
    source = ["G0 X0", "G0 X1"]

    preview_policy.configure_toolpath_preview(app, "job.nc", ["G0 X0"], source, preview_only=False)

    assert app.toolpath_panel.enabled is True
    assert app.toolpath_panel.top_view is None
