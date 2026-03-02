import pytest

from simple_sender.ui.dialogs import error_dialogs_ui

pytestmark = pytest.mark.unit


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value


class _Popup:
    def __init__(self) -> None:
        self.last_title = ""
        self.visible = False

    def title(self, text: str) -> None:
        self.last_title = text

    def deiconify(self) -> None:
        self.visible = True

    def lift(self) -> None:
        self.visible = True


def _make_popup_vars():
    return {
        "title": _Var(""),
        "time": _Var(""),
        "code": _Var(""),
        "definition": _Var(""),
    }


def test_show_grbl_code_popup_respects_enabled_toggle(monkeypatch) -> None:
    class _App:
        _closing = False
        grbl_popup_enabled = _Var(False)

    monkeypatch.setattr(
        error_dialogs_ui,
        "_ensure_grbl_code_popup",
        lambda _app: (_Popup(), _make_popup_vars()),
    )

    error_dialogs_ui.show_grbl_code_popup(_App(), "error:2")


def test_show_grbl_code_popup_dedupes_by_code(monkeypatch) -> None:
    class _StreamingController:
        def __init__(self) -> None:
            self.logs = []

        def handle_log(self, message: str) -> None:
            self.logs.append(message)

    class _App:
        def __init__(self) -> None:
            self._closing = False
            self.grbl_popup_enabled = _Var(True)
            self.grbl_popup_dedupe_sec = _Var(5.0)
            self.grbl_popup_auto_dismiss_sec = _Var(0.0)
            self._grbl_code_popup_last_ts_by_code = {}
            self._grbl_code_popup_last_suppressed_log_ts_by_code = {}
            self.streaming_controller = _StreamingController()

    app = _App()
    popup = _Popup()
    popup_vars = _make_popup_vars()
    called = {"count": 0}

    def _ensure(_app):
        called["count"] += 1
        return popup, popup_vars

    monotonic_values = iter([100.0, 101.0, 107.0])
    monkeypatch.setattr(error_dialogs_ui, "_ensure_grbl_code_popup", _ensure)
    monkeypatch.setattr(error_dialogs_ui, "center_window", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(error_dialogs_ui.time, "monotonic", lambda: next(monotonic_values))

    error_dialogs_ui.show_grbl_code_popup(app, "error:2")
    error_dialogs_ui.show_grbl_code_popup(app, "error:2")
    error_dialogs_ui.show_grbl_code_popup(app, "error:2")

    assert called["count"] == 2
    assert popup_vars["code"].get().startswith("Alarm/Error # 2")
    assert len(app.streaming_controller.logs) == 1
    assert "Suppressed duplicate GRBL popup" in app.streaming_controller.logs[0]


def test_show_grbl_code_popup_schedules_auto_dismiss(monkeypatch) -> None:
    class _App:
        def __init__(self) -> None:
            self._closing = False
            self.grbl_popup_enabled = _Var(True)
            self.grbl_popup_dedupe_sec = _Var(0.0)
            self.grbl_popup_auto_dismiss_sec = _Var(2.5)
            self._grbl_code_popup_after_id = None
            self._grbl_code_popup_last_ts_by_code = {}
            self.after_calls = []
            self.cancel_calls = []

        def after(self, ms: int, _func):
            self.after_calls.append(ms)
            return "after-id"

        def after_cancel(self, after_id) -> None:
            self.cancel_calls.append(after_id)

    app = _App()
    popup = _Popup()
    popup_vars = _make_popup_vars()

    monkeypatch.setattr(
        error_dialogs_ui,
        "_ensure_grbl_code_popup",
        lambda _app: (popup, popup_vars),
    )
    monkeypatch.setattr(error_dialogs_ui, "center_window", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(error_dialogs_ui.time, "monotonic", lambda: 100.0)

    error_dialogs_ui.show_grbl_code_popup(app, "ALARM:1")

    assert app.after_calls == [2500]
    assert app._grbl_code_popup_after_id == "after-id"
