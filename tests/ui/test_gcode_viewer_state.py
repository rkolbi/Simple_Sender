import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.viewer.gcode_viewer import GcodeViewer

pytestmark = pytest.mark.ui


class _DummyText:
    def __init__(self) -> None:
        self.tags = []

    def config(self, **_kwargs) -> None:
        return None

    def tag_remove(self, tag: str, start: str, end: str) -> None:
        self.tags.append((tag, start, end))

    def tag_add(self, tag: str, start: str, end: str) -> None:
        self.tags.append((tag, start, end))

    def see(self, _where) -> None:
        return None

    def dlineinfo(self, _where):
        return None


def _make_viewer() -> GcodeViewer:
    viewer = GcodeViewer.__new__(GcodeViewer)
    viewer.text = _DummyText()
    viewer.lines_count = 0
    viewer._sent_upto = 2
    viewer._acked_upto = 1
    viewer._current_idx = 0
    viewer._virtual_enabled = False
    viewer._virtual_lines = []
    viewer._virtual_window_size = 0
    viewer._virtual_window_start = 0
    viewer._virtual_window_end = 0
    return viewer


def test_clear_highlights_resets_state() -> None:
    viewer = _make_viewer()

    viewer.clear_highlights()

    assert viewer._sent_upto == -1
    assert viewer._acked_upto == -1
    assert viewer._current_idx == -1


def test_mark_sent_upto_ignores_invalid() -> None:
    viewer = _make_viewer()
    viewer.lines_count = 0

    viewer.mark_sent_upto(-1)

    assert viewer.text.tags == []


def test_highlight_current_noop_when_same() -> None:
    viewer = _make_viewer()
    viewer.lines_count = 3
    viewer._current_idx = 1

    viewer.highlight_current(1)

    assert viewer.text.tags == []
