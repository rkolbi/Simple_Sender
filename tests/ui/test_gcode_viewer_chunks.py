import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.viewer.gcode_viewer import GcodeViewer
from simple_sender.utils.constants import LINE_NUMBER_OFFSET

pytestmark = pytest.mark.ui


class _DummyText:
    def __init__(self) -> None:
        self.inserted = ""
        self.state = None

    def config(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]

    def delete(self, _start, _end) -> None:
        self.inserted = ""

    def insert(self, _where, text) -> None:
        self.inserted += text

    def tag_remove(self, *_args, **_kwargs) -> None:
        return None

    def tag_add(self, *_args, **_kwargs) -> None:
        return None

    def see(self, _where) -> None:
        return None

    def dlineinfo(self, _where):
        return None


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
    viewer._insert_chunk_size = 20
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


def test_start_chunk_insert_inserts_lines_with_numbers() -> None:
    viewer = _make_viewer()
    progress_calls = []
    done = []

    def on_progress(done_count, total):
        progress_calls.append((done_count, total))

    def on_done():
        done.append(True)

    viewer.clear_highlights = lambda: None
    viewer.highlight_current = lambda _idx: None

    viewer._start_chunk_insert(["G0 X0", "G1 X1"], chunk_size=5, on_done=on_done, on_progress=on_progress)

    assert "G0 X0" in viewer.text.inserted
    assert "G1 X1" in viewer.text.inserted
    assert progress_calls[0] == (0, 2)
    assert progress_calls[-1] == (2, 2)
    assert done
    assert viewer._insert_chunk_size == 20


def test_line_range_uses_offset() -> None:
    viewer = _make_viewer()
    start, end = viewer._line_range(0)

    assert start == f"{LINE_NUMBER_OFFSET}.0"
    assert end == f"{LINE_NUMBER_OFFSET + 1}.0"


def test_cancel_chunk_insert_resets_state() -> None:
    viewer = _make_viewer()
    viewer._insert_after_id = "id"
    viewer._insert_lines = ["G0"]
    viewer._insert_index = 3
    viewer._insert_done_cb = lambda: None
    viewer._insert_progress_cb = lambda _done, _total: None
    canceled = []
    viewer.after_cancel = lambda after_id: canceled.append(after_id)

    viewer._cancel_chunk_insert()

    assert viewer._insert_after_id is None
    assert viewer._insert_lines == []
    assert viewer._insert_index == 0
    assert viewer._insert_done_cb is None
    assert viewer._insert_progress_cb is None
    assert canceled == ["id"]
