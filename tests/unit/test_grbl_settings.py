import queue

import pytest

tk = pytest.importorskip("tkinter")

from simple_sender.ui.grbl_settings import (
    GRBLSettingsController,
    parse_setting_float,
    parse_setting_index,
    parse_setting_line,
)


class _Status:
    def __init__(self) -> None:
        self.text = None

    def config(self, text: str) -> None:
        self.text = text


class _App:
    def __init__(self) -> None:
        self.status = _Status()
        self._last_gcode_lines = []
        self._grbl_setting_info = {
            "$110": {"name": "X rate", "units": "mm/min", "desc": "X max rate"},
        }
        self._grbl_setting_keys = [110]
        self._rapid_rates = None
        self._rapid_rates_source = None
        self._accel_rates = None

    def _update_gcode_stats(self, _lines) -> None:
        self.stats_called = True


class _Text:
    def __init__(self) -> None:
        self.state = None
        self.contents = ""

    def config(self, state=None):
        if state is not None:
            self.state = state

    def delete(self, _start, _end) -> None:
        self.contents = ""

    def insert(self, _end, text: str) -> None:
        self.contents += text


class _Tree:
    def __init__(self) -> None:
        self.values = {}
        self.tags = {}

    def set(self, item, column, value) -> None:
        self.values[(item, column)] = value

    def item(self, item, option=None, **kwargs):
        if kwargs:
            if "tags" in kwargs:
                self.tags[item] = list(kwargs["tags"])
            return None
        if option == "tags":
            return tuple(self.tags.get(item, []))
        return {}


class _StateTree:
    def __init__(self) -> None:
        self._state = set()

    def state(self, statespec=None):
        if statespec is None:
            return tuple(sorted(self._state))
        for spec in statespec:
            if isinstance(spec, str) and spec.startswith("!"):
                self._state.discard(spec[1:])
            else:
                self._state.add(spec)
        return tuple(sorted(self._state))


class _Entry:
    def __init__(self, value: str) -> None:
        self._value = value
        self.destroyed = False

    def get(self) -> str:
        return self._value

    def destroy(self) -> None:
        self.destroyed = True


class _FocusDialog:
    def winfo_exists(self) -> bool:
        return True


class _FocusWidget:
    def __init__(self, master=None) -> None:
        self.master = master

    def winfo_toplevel(self):
        return self.master if self.master is not None else self


class _FocusEntry:
    def __init__(self, dialog, focus_widget) -> None:
        self._numeric_keypad_dialog = dialog
        self._focus_widget = focus_widget

    def focus_get(self):
        return self._focus_widget


class _Button:
    def __init__(self) -> None:
        self._state = "normal"

    def cget(self, key: str):
        if key == "state":
            return self._state
        raise KeyError(key)

    def config(self, *, state: str) -> None:
        self._state = state


class _ConnectedGrbl:
    def is_connected(self) -> bool:
        return True

    def is_streaming(self) -> bool:
        return False


class _SaveFailureApp(_App):
    def __init__(self) -> None:
        super().__init__()
        self.grbl = _ConnectedGrbl()
        self.ui_q = queue.Queue()
        self._after_callbacks = []

    def _send_manual(self, _command: str, _source: str) -> None:
        raise RuntimeError("boom")

    def after(self, _delay: int, callback) -> None:
        self._after_callbacks.append(callback)


def test_start_capture_resets_state_and_renders_header() -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    controller.settings_raw_text = _Text()

    controller.start_capture("Header")

    assert controller._settings_capture is True
    assert controller._settings_data == {}
    assert controller._settings_edited == {}
    assert controller._settings_raw_lines == []
    assert "Header" in controller.settings_raw_text.contents


def test_handle_line_parses_settings_and_ok_triggers_refresh() -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    controller._settings_capture = True
    controller._render_settings = lambda: setattr(controller, "render_called", True)
    controller._update_rapid_rates = lambda: setattr(controller, "rapid_called", True)
    controller._update_accel_rates = lambda: setattr(controller, "accel_called", True)
    controller._render_settings_raw = lambda header=None: setattr(
        controller, "raw_called", header if header is not None else True
    )

    controller.handle_line("$110=500")
    controller.handle_line("note")
    controller.handle_line("ok")

    assert controller._settings_capture is False
    assert controller._settings_data["$110"][0] == "500"
    assert controller.render_called is True
    assert controller.rapid_called is True
    assert controller.accel_called is True
    assert controller.raw_called is True


def test_handle_line_error_updates_status() -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    controller._settings_capture = True
    controller._render_settings_raw = lambda header=None: setattr(controller, "raw_called", True)

    controller.handle_line("error:7")

    assert controller._settings_capture is False
    assert "Settings error" in app.status.text
    assert controller.raw_called is True


def test_update_rapid_and_accel_rates() -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    controller._settings_data = {
        "$110": ("800", 110),
        "$111": ("900", 111),
        "$112": ("1000", 112),
        "$120": ("10", 120),
        "$121": ("20", 121),
        "$122": ("30", 122),
    }

    controller._update_rapid_rates()
    controller._update_accel_rates()

    assert app._rapid_rates == (800.0, 900.0, 1000.0)
    assert app._rapid_rates_source == "grbl"
    assert app._accel_rates == (10.0, 20.0, 30.0)


def test_commit_pending_setting_edit_updates_values(monkeypatch) -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    controller.settings_tree = _Tree()
    controller._settings_baseline = {"$110": "500"}
    controller._settings_values = {"$110": "500"}
    controller._settings_edited = {}
    controller._settings_items = {"$110": "item1"}
    entry = _Entry("600")
    controller._settings_edit_entry = entry
    controller._settings_entry_meta = {entry: ("$110", "item1")}

    controller._commit_pending_setting_edit()

    assert controller.settings_tree.values[("item1", "value")] == "600"
    assert controller._settings_values["$110"] == "600"
    assert controller._settings_edited["$110"] == "600"
    assert "edited" in controller.settings_tree.tags["item1"]
    assert entry.destroyed is True


def test_commit_pending_setting_edit_rejects_out_of_range(monkeypatch) -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    controller.settings_tree = _Tree()
    controller._settings_baseline = {"$110": "500"}
    controller._settings_values = {"$110": "500"}
    controller._settings_edited = {}
    controller._settings_items = {"$110": "item1"}
    entry = _Entry("-1")
    controller._settings_edit_entry = entry
    controller._settings_entry_meta = {entry: ("$110", "item1")}
    warnings = []

    monkeypatch.setattr(
        "simple_sender.ui.grbl_settings.messagebox.showwarning",
        lambda title, message: warnings.append((title, message)),
    )

    controller._commit_pending_setting_edit()

    assert controller._settings_values["$110"] == "500"
    assert controller._settings_edited == {}
    assert warnings
    assert entry.destroyed is True


def test_set_settings_edit_enabled_restores_tree_state() -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    controller.settings_tree = _StateTree()

    controller._set_settings_edit_enabled(False)
    assert "disabled" in controller.settings_tree.state()

    controller._set_settings_edit_enabled(True)
    assert "disabled" not in controller.settings_tree.state()


def test_focus_out_targets_numeric_keypad_when_focus_moves_into_dialog() -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    dialog = _FocusDialog()
    focus_widget = _FocusWidget(master=dialog)
    entry = _FocusEntry(dialog, focus_widget)

    assert controller._focus_out_targets_numeric_keypad(entry) is True


def test_focus_out_targets_numeric_keypad_false_when_focus_leaves_dialog() -> None:
    app = _App()
    controller = GRBLSettingsController(app)
    dialog = _FocusDialog()
    focus_widget = _FocusWidget(master=None)
    entry = _FocusEntry(dialog, focus_widget)

    assert controller._focus_out_targets_numeric_keypad(entry) is False


def test_save_changes_failure_callback_restores_controls(monkeypatch) -> None:
    app = _SaveFailureApp()
    controller = GRBLSettingsController(app)
    controller.settings_tree = _StateTree()
    controller.btn_save = _Button()
    controller.btn_refresh = _Button()
    controller._settings_edited = {"$110": "600"}

    monkeypatch.setattr(
        "simple_sender.ui.grbl_settings.messagebox.askyesno",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr("simple_sender.ui.grbl_settings.time.sleep", lambda *_args, **_kwargs: None)

    class _ImmediateThread:
        def __init__(self, *, target, daemon=True) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    monkeypatch.setattr("simple_sender.ui.grbl_settings.threading.Thread", _ImmediateThread)

    controller.save_changes()

    assert controller._settings_saving is True
    assert "disabled" in controller.settings_tree.state()
    assert controller.btn_save.cget("state") == "disabled"
    assert controller.btn_refresh.cget("state") == "disabled"
    assert app._after_callbacks

    for callback in list(app._after_callbacks):
        callback()

    assert controller._settings_saving is False
    assert "disabled" not in controller.settings_tree.state()
    assert controller.btn_save.cget("state") == "normal"
    assert controller.btn_refresh.cget("state") == "normal"
    assert app.status.text == "Settings save failed: boom"

    logs = [entry[1] for entry in list(app.ui_q.queue) if entry[0] == "log"]
    assert any("[settings] Save failed: boom" in line for line in logs)


def test_parse_setting_line_handles_valid_and_non_numeric_indices() -> None:
    assert parse_setting_line("$110=500") == ("$110", "500", 110)
    assert parse_setting_line("$foo=bar") == ("$foo", "bar", None)


def test_parse_setting_line_rejects_non_setting_lines() -> None:
    assert parse_setting_line("ok") is None
    assert parse_setting_line("error:7") is None
    assert parse_setting_line("M3 S1000") is None


def test_parse_setting_index_handles_valid_invalid_keys() -> None:
    assert parse_setting_index("$110") == 110
    assert parse_setting_index("$foo") is None
    assert parse_setting_index("110") is None


def test_parse_setting_float_reads_numeric_values() -> None:
    data = {"$110": ("500.5", 110), "$120": ("bad", 120)}

    assert parse_setting_float(data, "$110") == 500.5
    assert parse_setting_float(data, "$120") is None
    assert parse_setting_float(data, "$999") is None
