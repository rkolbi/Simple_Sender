import queue

import pytest

from simple_sender.ui import app_lifecycle

pytestmark = pytest.mark.unit


def test_format_exception_includes_type() -> None:
    try:
        raise ValueError("boom")
    except ValueError as exc:
        text = app_lifecycle.format_exception(exc)

    assert "ValueError" in text
    assert "boom" in text


def test_log_exception_main_thread_logs_to_streaming_controller(monkeypatch) -> None:
    logs = []

    class _Controller:
        def handle_log(self, message: str) -> None:
            logs.append(message)

    app = type("App", (), {})()
    app.streaming_controller = _Controller()
    app.ui_q = queue.Queue()
    app._should_show_error_dialog = lambda: False
    app._post_ui_thread = lambda *_args, **_kwargs: None

    sentinel = object()
    monkeypatch.setattr(app_lifecycle.threading, "current_thread", lambda: sentinel)
    monkeypatch.setattr(app_lifecycle.threading, "main_thread", lambda: sentinel)

    app_lifecycle.log_exception(app, "Context", ValueError("boom"))

    assert logs[0] == "[error] Context: boom"
    assert any("ValueError" in line for line in logs)
    assert app.ui_q.empty()


def test_log_exception_background_thread_queues_lines(monkeypatch) -> None:
    app = type("App", (), {})()
    app.streaming_controller = type("Controller", (), {"handle_log": lambda *_args: None})()
    app.ui_q = queue.Queue()
    app._should_show_error_dialog = lambda: False
    app._post_ui_thread = lambda *_args, **_kwargs: None

    monkeypatch.setattr(app_lifecycle.threading, "current_thread", lambda: object())
    monkeypatch.setattr(app_lifecycle.threading, "main_thread", lambda: object())

    app_lifecycle.log_exception(
        app,
        "Background",
        RuntimeError("fail"),
        traceback_text="line1\nline2",
    )

    events = list(app.ui_q.queue)
    assert events[0] == ("log", "[error] Background: fail")
    assert events[1:] == [("log", "line1"), ("log", "line2")]


def test_log_exception_requests_dialog(monkeypatch) -> None:
    app = type("App", (), {})()
    app.streaming_controller = type("Controller", (), {"handle_log": lambda *_args: None})()
    app.ui_q = queue.Queue()
    app._should_show_error_dialog = lambda: True
    posted = []
    app._post_ui_thread = lambda func, *args: posted.append((func, args))

    monkeypatch.setattr(app_lifecycle.messagebox, "showerror", lambda *_args: None)

    app_lifecycle.log_exception(
        app,
        "Dialog",
        RuntimeError("boom"),
        show_dialog=True,
        dialog_title="Oops",
        traceback_text="details",
    )

    assert posted
    func, args = posted[0]
    assert args == ("Oops", "details")


def test_tk_report_callback_exception_forwards_to_log(monkeypatch) -> None:
    calls = {}

    def _log(app, context, exc, **kwargs):
        calls["context"] = context
        calls["exc"] = exc
        calls.update(kwargs)

    monkeypatch.setattr(app_lifecycle, "log_exception", _log)

    app_lifecycle.tk_report_callback_exception(object(), ValueError, ValueError("oops"), None)

    assert calls["context"] == "Unhandled UI exception"
    assert isinstance(calls["exc"], ValueError)
    assert calls["show_dialog"] is True
    assert calls["dialog_title"] == "Application error"
    assert "ValueError" in calls["traceback_text"]


def test_on_close_calls_shutdown_sequence() -> None:
    calls = []

    class _Grbl:
        def disconnect(self) -> None:
            calls.append("disconnect")

    class _Py:
        def quit(self) -> None:
            calls.append("quit")

    class _App:
        def __init__(self) -> None:
            self._closing = False
            self.grbl = _Grbl()

        def _save_settings(self) -> None:
            calls.append("save")

        def _log_exception(self, *_args) -> None:
            calls.append("log")

        def _stop_joystick_hold(self) -> None:
            calls.append("hold")

        def _stop_joystick_polling(self) -> None:
            calls.append("poll")

        def _get_pygame_module(self):
            return _Py()

        def destroy(self) -> None:
            calls.append("destroy")

    app = _App()

    app_lifecycle.on_close(app)

    assert app._closing is True
    assert calls == ["save", "disconnect", "hold", "poll", "quit", "destroy"]


def test_on_close_closes_grbl_popup(monkeypatch) -> None:
    calls = []

    class _Grbl:
        def disconnect(self) -> None:
            calls.append("disconnect")

    class _App:
        def __init__(self) -> None:
            self._closing = False
            self.grbl = _Grbl()

        def _save_settings(self) -> None:
            calls.append("save")

        def _log_exception(self, *_args) -> None:
            calls.append("log")

        def _stop_joystick_hold(self) -> None:
            calls.append("hold")

        def _stop_joystick_polling(self) -> None:
            calls.append("poll")

        def _get_pygame_module(self):
            return None

        def destroy(self) -> None:
            calls.append("destroy")

    monkeypatch.setattr(
        app_lifecycle,
        "close_grbl_code_popup",
        lambda _app: calls.append("popup_close"),
    )
    app = _App()

    app_lifecycle.on_close(app)

    assert calls[0] == "popup_close"


def test_on_close_logs_save_failure() -> None:
    calls = []

    class _Grbl:
        def disconnect(self) -> None:
            calls.append("disconnect")

    class _App:
        def __init__(self) -> None:
            self._closing = False
            self.grbl = _Grbl()

        def _save_settings(self) -> None:
            raise RuntimeError("fail")

        def _log_exception(self, *_args) -> None:
            calls.append("log")

        def _stop_joystick_hold(self) -> None:
            calls.append("hold")

        def _stop_joystick_polling(self) -> None:
            calls.append("poll")

        def _get_pygame_module(self):
            return None

        def destroy(self) -> None:
            calls.append("destroy")

    app = _App()

    app_lifecycle.on_close(app)

    assert app._closing is True
    assert "log" in calls
    assert "disconnect" not in calls
    assert calls[-2:] == ["poll", "destroy"]


def test_on_close_shuts_down_accessory_router() -> None:
    calls = []

    class _Router:
        def shutdown(self, timeout: float = 0.0) -> None:
            calls.append(("shutdown", timeout))

    class _Grbl:
        def disconnect(self) -> None:
            calls.append("disconnect")

    class _App:
        def __init__(self) -> None:
            self._closing = False
            self.grbl = _Grbl()
            self.accessory_router = _Router()

        def _save_settings(self) -> None:
            calls.append("save")

        def _log_exception(self, *_args) -> None:
            calls.append("log")

        def _stop_joystick_hold(self) -> None:
            calls.append("hold")

        def _stop_joystick_polling(self) -> None:
            calls.append("poll")

        def _get_pygame_module(self):
            return None

        def destroy(self) -> None:
            calls.append("destroy")

    app = _App()

    app_lifecycle.on_close(app)

    assert ("shutdown", 1.0) in calls
