import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.toolpath import ToolpathPanel

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _View:
    def __init__(self) -> None:
        self.lines = None
        self.hash = None
        self.streaming = None
        self.visible = True
        self.job_name = None

    def set_gcode_async(self, lines, *, lines_hash=None):
        self.lines = lines
        self.hash = lines_hash

    def set_job_name(self, name: str) -> None:
        self.job_name = name

    def set_streaming_mode(self, enabled: bool) -> None:
        self.streaming = enabled

    def set_visible(self, visible: bool) -> None:
        self.visible = visible


class _TopView:
    def __init__(self) -> None:
        self.lines = None
        self.visible = True
        self.cleared = False
        self.job_name = None

    def set_visible(self, visible: bool) -> None:
        self.visible = visible

    def set_lines(self, lines, **_kwargs):
        self.lines = lines

    def clear(self) -> None:
        self.cleared = True

    def set_job_name(self, name: str) -> None:
        self.job_name = name


def test_set_gcode_lines_defers_when_hidden() -> None:
    app = type("App", (), {})()
    panel = ToolpathPanel(app)
    panel.view = _View()
    panel.view._visible = False

    panel.set_gcode_lines(["G0 X0"], lines_hash="hash")

    assert panel._pending_gcode_lines == ["G0 X0"]
    assert panel._pending_gcode_hash == "hash"


def test_set_visible_flushes_pending_lines() -> None:
    app = type("App", (), {})()
    panel = ToolpathPanel(app)
    panel.view = _View()
    panel._pending_gcode_lines = ["G1 X1"]
    panel._pending_gcode_hash = "hash"

    panel.set_visible(True)

    assert panel.view.lines == ["G1 X1"]
    assert panel.view.hash == "hash"


def test_set_top_view_visible_flushes_pending() -> None:
    app = type("App", (), {})()
    panel = ToolpathPanel(app)
    panel.top_view = _TopView()
    panel._pending_top_request = (["G0 X0"], None, None)

    panel.set_top_view_visible(True)

    assert panel.top_view.lines == ["G0 X0"]


def test_set_streaming_updates_view() -> None:
    app = type("App", (), {})()
    panel = ToolpathPanel(app)
    panel.view = _View()

    panel.set_streaming(True)

    assert panel._streaming
    assert panel.view.streaming is True


def test_apply_parse_result_defers_when_unbuilt() -> None:
    app = type("App", (), {})()
    panel = ToolpathPanel(app)
    result = type("Result", (), {"segments": [1], "bounds": (0, 1, 0, 1, 0, 0)})()

    panel.apply_parse_result(["G0 X0"], result, lines_hash="hash")

    assert panel._pending_parsed == (["G0 X0"], result, "hash")
    assert panel._pending_top_parsed == (result, "hash")


def test_set_top_view_lines_immediate_when_visible() -> None:
    app = type("App", (), {})()
    panel = ToolpathPanel(app)
    panel.top_view = _TopView()
    panel.top_view._visible = True

    panel.set_top_view_lines(["G0 X0"], max_segments=2, arc_step_rad=0.1)

    assert panel.top_view.lines == ["G0 X0"]
    assert panel._pending_top_request is None


def test_clear_resets_pending_and_views() -> None:
    app = type("App", (), {})()
    panel = ToolpathPanel(app)
    panel.view = _View()
    panel.top_view = _TopView()
    panel._pending_gcode_lines = ["G1 X1"]
    panel._pending_top_request = (["G1 X1"], None, None)

    panel.clear()

    assert panel._pending_gcode_lines is None
    assert panel._pending_top_request is None
    assert panel.view.lines == []
    assert panel.view.job_name == ""
    assert panel.top_view.cleared is True
