import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.controls import toolbar

pytestmark = pytest.mark.ui


class _Var:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _DummyWidget:
    def __init__(self, *_args, **kwargs) -> None:
        self.kwargs = kwargs
        self.text = kwargs.get("text", "")
        self.state = kwargs.get("state")
        self.style = kwargs.get("style", "")
        self.mapped = True

    def pack(self, **_kwargs) -> None:
        return None

    def pack_forget(self) -> None:
        self.mapped = False

    def grid(self, **_kwargs) -> None:
        return None

    def winfo_exists(self) -> bool:
        return True

    def winfo_ismapped(self) -> bool:
        return bool(self.mapped)

    def cget(self, key: str):
        if key == "style":
            return self.style
        if key == "text":
            return self.text
        if key == "state":
            return self.state
        if key == "background":
            return self.kwargs.get("background", "")
        return self.kwargs.get(key)

    def config(self, **kwargs) -> None:
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "style" in kwargs:
            self.style = kwargs["style"]
        if "state" in kwargs:
            self.state = kwargs["state"]


class _DummyButton(_DummyWidget):
    pass


class _DummyLabel(_DummyWidget):
    pass


class _DummyFrame(_DummyWidget):
    pass


class _DummyCombobox(_DummyWidget):
    def __setitem__(self, _key, _value) -> None:
        return None


class _DummySeparator(_DummyWidget):
    pass


class _DummyStyle:
    def lookup(self, _style: str, _option: str):
        return ""

    def configure(self, _style: str, **_kwargs) -> None:
        return None

    def map(self, _style: str, **_kwargs) -> None:
        return None


class _DummyTtk:
    Frame = _DummyFrame
    Label = _DummyLabel
    Button = _DummyButton
    Combobox = _DummyCombobox
    Separator = _DummySeparator
    Style = _DummyStyle


class _DummyFont:
    def cget(self, key: str):
        if key == "size":
            return 10
        if key == "family":
            return "Arial"
        if key == "weight":
            return "normal"
        return ""


class _DummyTkFont:
    @staticmethod
    def nametofont(_name: str):
        return _DummyFont()

    class Font(_DummyFont):
        def __init__(self, **_kwargs) -> None:
            super().__init__()


class _DummyTk:
    Label = _DummyLabel


def test_build_toolbar_creates_controls(monkeypatch) -> None:
    monkeypatch.setattr(toolbar, "ttk", _DummyTtk)
    monkeypatch.setattr(toolbar, "tk", _DummyTk)
    monkeypatch.setattr(toolbar, "tkfont", _DummyTkFont)
    monkeypatch.setattr(toolbar, "icon_label", lambda _icon, label: label)
    kb_calls = []
    monkeypatch.setattr(toolbar, "set_kb_id", lambda _btn, key: kb_calls.append(key))
    monkeypatch.setattr(toolbar, "apply_tooltip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(toolbar, "attach_log_gcode", lambda *_args, **_kwargs: None)

    class _Grbl:
        def unlock(self) -> None:
            return None

    class _App:
        def __init__(self) -> None:
            self.icon_button_style = "style"
            self.current_port = _Var("")
            self.machine_state = _Var("")
            self._manual_controls = []
            self._offline_controls = set()
            self.grbl = _Grbl()

        def cget(self, _key: str):
            return ""

        def _confirm_and_run(self, _label: str, func):
            return func

        def _run_if_connected(self, func):
            return func

        def refresh_ports(self):
            return None

        def toggle_connect(self):
            return None

        def open_gcode(self):
            return None

        def _clear_gcode(self):
            return None

        def run_job(self):
            return None

        def pause_job(self):
            return None

        def resume_job(self):
            return None

        def stop_job(self):
            return None

        def _show_resume_dialog(self):
            return None

        def _show_alarm_recovery(self):
            return None

        def _update_resume_button_visibility(self):
            return None

        def _update_recover_button_visibility(self):
            return None

    app = _App()

    toolbar.build_toolbar(app)

    assert hasattr(app, "btn_run")
    assert hasattr(app, "btn_pause")
    assert hasattr(app, "btn_resume")
    assert app.btn_run.state == "disabled"
    assert "port_refresh" in kb_calls
    assert "job_run" in kb_calls
    assert app.btn_open in app._manual_controls
    assert app.btn_clear in app._offline_controls


def test_update_job_button_mode_disables_autolevel_for_leveled_file(monkeypatch) -> None:
    monkeypatch.setattr(toolbar, "apply_tooltip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(toolbar, "set_kb_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(toolbar, "icon_label", lambda _icon, label: label)

    class _App:
        def __init__(self) -> None:
            self.btn_open = _DummyButton(text="Auto-Level")
            self._gcode_streaming_mode = False
            self._last_gcode_path = "job-AL.gcode"
            self.auto_level_enabled = True
            self._job_button_mode = None
            self._job_button_streaming = None
            self.job_button_hint = None

        def _confirm_and_run(self, _label: str, func):
            return func

        def _set_manual_controls_enabled(self, _ready: bool) -> None:
            return None

    app = _App()

    toolbar.update_job_button_mode(app, "auto_level")

    assert app.btn_open._force_disabled is True
    assert "already leveled" in app.btn_open._disabled_reason.lower()


def test_update_job_button_mode_reads_tk_var_and_keeps_read_job_enabled_when_autolevel_off(
    monkeypatch,
) -> None:
    monkeypatch.setattr(toolbar, "apply_tooltip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(toolbar, "set_kb_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(toolbar, "icon_label", lambda _icon, label: label)

    class _App:
        def __init__(self) -> None:
            self.btn_open = _DummyButton(text="Auto-Level")
            self._gcode_streaming_mode = False
            self._last_gcode_path = "job.gcode"
            self.auto_level_enabled = _Var(False)
            self._job_button_mode = None
            self._job_button_streaming = None
            self.job_button_hint = None
            self._offline_controls = set()

        def open_gcode(self):
            return None

        def _set_manual_controls_enabled(self, _ready: bool) -> None:
            return None

    app = _App()

    toolbar.update_job_button_mode(app, "auto_level")

    assert app._job_button_mode == "read_job"
    assert app.btn_open.text == "Read Job"
    assert app.btn_open._force_disabled is False
    assert app.btn_open.state != "disabled"


def test_refresh_toolbar_action_focus_highlights_connect_when_disconnected(monkeypatch) -> None:
    monkeypatch.setattr(toolbar, "ttk", _DummyTtk)

    class _App:
        def __init__(self) -> None:
            self.style = _DummyStyle()
            self.icon_button_style = "SimpleSender.IconButton.TButton"
            self.theme_palette = {}
            self.connected = False
            self._grbl_ready = False
            self._status_seen = False
            self._alarm_locked = False
            self._stream_state = None
            self.btn_conn = _DummyButton(style=self.icon_button_style, state="normal")
            self.btn_open = _DummyButton(style=self.icon_button_style, state="normal")
            self.btn_run = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_pause = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_resume = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_resume_from = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_stop = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_unlock_top = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_alarm_recover = _DummyButton(style=self.icon_button_style, state="disabled")
            self._toolbar_group_title_labels = {
                "Connection": _DummyLabel(style=""),
                "Job": _DummyLabel(style=""),
                "Run": _DummyLabel(style=""),
                "Recovery": _DummyLabel(style=""),
            }

    app = _App()
    toolbar.refresh_toolbar_action_focus(app)

    assert (
        app._toolbar_group_title_labels["Connection"].style
        == app.toolbar_group_focus_label_style
    )
    assert app.btn_open.style == app.icon_button_style


def test_refresh_toolbar_action_focus_highlights_pause_while_running(monkeypatch) -> None:
    monkeypatch.setattr(toolbar, "ttk", _DummyTtk)

    class _App:
        def __init__(self) -> None:
            self.style = _DummyStyle()
            self.icon_button_style = "SimpleSender.IconButton.TButton"
            self.theme_palette = {}
            self.connected = True
            self._grbl_ready = True
            self._status_seen = True
            self._alarm_locked = False
            self._stream_state = "running"
            self.btn_conn = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_open = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_run = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_pause = _DummyButton(style=self.icon_button_style, state="normal")
            self.btn_resume = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_resume_from = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_stop = _DummyButton(style=self.icon_button_style, state="normal")
            self.btn_unlock_top = _DummyButton(style=self.icon_button_style, state="disabled")
            self.btn_alarm_recover = _DummyButton(style=self.icon_button_style, state="disabled")
            self._last_gcode_lines = ["G1 X0"]
            self._toolbar_group_title_labels = {
                "Connection": _DummyLabel(style=""),
                "Job": _DummyLabel(style=""),
                "Run": _DummyLabel(style=""),
                "Recovery": _DummyLabel(style=""),
            }

    app = _App()
    toolbar.refresh_toolbar_action_focus(app)

    assert app._toolbar_group_title_labels["Run"].style == app.toolbar_group_focus_label_style
    assert app.btn_pause.style == app.icon_button_style
    assert app.btn_stop.style == app.icon_button_style
