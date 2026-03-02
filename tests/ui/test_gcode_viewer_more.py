import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.viewer.gcode_viewer import GcodeViewer
from simple_sender.utils.constants import LINE_NUMBER_OFFSET

pytestmark = pytest.mark.ui


class _DummyText:
    def __init__(self) -> None:
        self.tags = []

    def config(self, **_kwargs) -> None:
        return None

    def tag_add(self, tag: str, start: str, end: str) -> None:
        self.tags.append((tag, start, end))

    def tag_remove(self, tag: str, start: str, end: str) -> None:
        self.tags.append((f"remove:{tag}", start, end))

    def see(self, _where) -> None:
        return None

    def dlineinfo(self, _where):
        return None


def _make_viewer() -> GcodeViewer:
    viewer = GcodeViewer.__new__(GcodeViewer)
    viewer.text = _DummyText()
    viewer.lines_count = 2
    viewer._sent_upto = -1
    viewer._acked_upto = -1
    viewer._current_idx = -1
    viewer._virtual_enabled = False
    viewer._virtual_lines = []
    viewer._virtual_window_size = 0
    viewer._virtual_window_start = 0
    viewer._virtual_window_end = 0
    return viewer


def test_highlight_current_clears_on_negative() -> None:
    viewer = _make_viewer()

    viewer.highlight_current(1)
    viewer.highlight_current(-1)

    assert ("current", f"{1 + LINE_NUMBER_OFFSET}.0", f"{2 + LINE_NUMBER_OFFSET}.0") in viewer.text.tags
    assert ("remove:current", f"{1 + LINE_NUMBER_OFFSET}.0", f"{2 + LINE_NUMBER_OFFSET}.0") in viewer.text.tags
