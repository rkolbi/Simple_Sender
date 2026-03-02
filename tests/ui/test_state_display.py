import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.status import state_display

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _Gview:
    def __init__(self, lines_count: int) -> None:
        self.lines_count = lines_count
        self.current = None

    def highlight_current(self, idx: int) -> None:
        self.current = idx


def test_update_current_highlight_machine_mode_tracks_planner_depth() -> None:
    class _App:
        current_line_mode = _Var("machine")
        gview = _Gview(100)
        _last_sent_index = 12
        _last_acked_index = 10
        _planner_blocks_available = 13
        _planner_blocks_capacity = 15
        _machine_state_text = "Run"

    app = _App()
    state_display.update_current_highlight(app)

    # 2 planner blocks queued => executing line is about one behind last ack.
    assert app.gview.current == 9


def test_update_current_highlight_machine_mode_uses_last_ack_when_motion_active() -> None:
    class _App:
        current_line_mode = _Var("machine")
        gview = _Gview(100)
        _last_sent_index = 12
        _last_acked_index = 10
        _planner_blocks_available = 15
        _planner_blocks_capacity = 15
        _machine_state_text = "Run"

    app = _App()
    state_display.update_current_highlight(app)

    # Planner may report empty while the final block is still executing.
    assert app.gview.current == 10
