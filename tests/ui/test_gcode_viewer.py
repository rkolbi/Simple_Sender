import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.viewer.gcode_viewer import (
    GcodeViewer,
    GCODE_VIEWER_CHUNK_SIZE_LARGE,
    GCODE_VIEWER_CHUNK_SIZE_MEDIUM,
    GCODE_VIEWER_CHUNK_SIZE_SMALL,
    GCODE_VIEWER_LARGE_FILE_THRESHOLD,
    GCODE_VIEWER_SMALL_FILE_THRESHOLD,
)
from simple_sender.utils.constants import LINE_NUMBER_OFFSET

pytestmark = pytest.mark.ui


class _DummyText:
    def __init__(self) -> None:
        self.inserted = ""
        self.tags = []
        self.state = None
        self.visible_lines: set[str] = set()
        self.see_calls: list[str] = []

    def config(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]

    def delete(self, _start, _end) -> None:
        self.inserted = ""

    def insert(self, _where, text) -> None:
        self.inserted += text

    def tag_add(self, tag: str, start: str, end: str) -> None:
        self.tags.append((tag, start, end))

    def tag_remove(self, tag: str, start: str, end: str) -> None:
        self.tags.append((f"remove:{tag}", start, end))

    def see(self, _where) -> None:
        self.see_calls.append(_where)
        return None

    def dlineinfo(self, where: str):
        return (0, 0, 10, 10, 0) if where in self.visible_lines else None


def test_highlight_current_updates_tags() -> None:
    viewer = _make_viewer()
    viewer.lines_count = 3

    viewer.highlight_current(1)
    viewer.highlight_current(2)

    assert ("current", f"{1 + LINE_NUMBER_OFFSET}.0", f"{2 + LINE_NUMBER_OFFSET}.0") in viewer.text.tags
    assert ("remove:current", f"{1 + LINE_NUMBER_OFFSET}.0", f"{2 + LINE_NUMBER_OFFSET}.0") in viewer.text.tags


def test_highlight_current_scrolls_only_when_line_not_visible() -> None:
    viewer = _make_viewer()
    viewer.lines_count = 4
    visible_line = f"{2 + LINE_NUMBER_OFFSET}.0"
    viewer.text.visible_lines.add(visible_line)

    viewer.highlight_current(2)
    viewer.highlight_current(3)

    assert viewer.text.see_calls == [f"{3 + LINE_NUMBER_OFFSET}.0"]


def _make_viewer() -> GcodeViewer:
    viewer = GcodeViewer.__new__(GcodeViewer)
    viewer.text = _DummyText()
    viewer.lines_count = 0
    viewer._sent_upto = -1
    viewer._acked_upto = -1
    viewer._current_idx = -1
    viewer._insert_after_id = None
    viewer._insert_lines = []
    viewer._insert_index = 0
    viewer._insert_chunk_size = GCODE_VIEWER_CHUNK_SIZE_SMALL
    viewer._insert_done_cb = None
    viewer._insert_progress_cb = None
    viewer._insert_progress_last_ts = 0.0
    viewer._insert_progress_interval_s = 0.0
    viewer._insert_time_budget_s = 0.1
    viewer._insert_max_chunks_per_tick = 8
    viewer._insert_delay_ms = 1
    viewer._virtual_enabled = False
    viewer._virtual_lines = []
    viewer._virtual_window_size = 0
    viewer._virtual_window_start = 0
    viewer._virtual_window_end = 0
    viewer.after = lambda _delay, func: func() or None
    viewer.after_cancel = lambda _id: None
    return viewer


def test_set_lines_selects_chunk_size() -> None:
    viewer = _make_viewer()
    sizes = []

    def _start(lines, chunk_size, on_done=None, on_progress=None):
        sizes.append(chunk_size)

    viewer._start_chunk_insert = _start

    viewer.set_lines(["G0"] * (GCODE_VIEWER_SMALL_FILE_THRESHOLD - 1))
    viewer.set_lines(["G0"] * (GCODE_VIEWER_SMALL_FILE_THRESHOLD + 1))
    viewer.set_lines(["G0"] * (GCODE_VIEWER_LARGE_FILE_THRESHOLD + 1))

    assert sizes == [
        GCODE_VIEWER_CHUNK_SIZE_SMALL,
        GCODE_VIEWER_CHUNK_SIZE_MEDIUM,
        GCODE_VIEWER_CHUNK_SIZE_LARGE,
    ]


def test_mark_sent_upto_adds_tag() -> None:
    viewer = _make_viewer()
    viewer.lines_count = 5

    viewer.mark_sent_upto(2)

    assert ("sent", f"{LINE_NUMBER_OFFSET}.0", f"{3 + LINE_NUMBER_OFFSET}.0") in viewer.text.tags


def test_mark_acked_upto_converts_sent_to_acked() -> None:
    viewer = _make_viewer()
    viewer.lines_count = 5
    viewer._sent_upto = 1

    viewer.mark_acked_upto(2)

    assert ("remove:sent", f"{LINE_NUMBER_OFFSET}.0", f"{3 + LINE_NUMBER_OFFSET}.0") in viewer.text.tags
    assert ("acked", f"{LINE_NUMBER_OFFSET}.0", f"{3 + LINE_NUMBER_OFFSET}.0") in viewer.text.tags


def test_set_lines_virtualized_shifts_window_on_current_highlight() -> None:
    viewer = _make_viewer()
    lines = [f"G1 X{idx}" for idx in range(10)]

    viewer.set_lines_virtualized(lines, window_size=4)
    viewer.highlight_current(8)

    assert viewer.lines_count == 10
    assert viewer._virtual_window_start <= 8 < viewer._virtual_window_end
