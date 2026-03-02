import pytest

from simple_sender.ui import main_tabs

pytestmark = pytest.mark.ui


class _ToolpathPanel:
    def __init__(self) -> None:
        self.visible = None
        self.top_visible = None

    def set_visible(self, value: bool) -> None:
        self.visible = value

    def set_top_view_visible(self, value: bool) -> None:
        self.top_visible = value


class _Notebook:
    def __init__(self, label: str) -> None:
        self._label = label

    def select(self):
        return "tab-id"

    def tab(self, _tab_id, _key):
        return self._label


def test_update_tab_visibility_sets_flags() -> None:
    class _App:
        notebook = _Notebook("3D View")
        toolpath_panel = _ToolpathPanel()

    app = _App()

    main_tabs.update_tab_visibility(app)

    assert app.toolpath_panel.visible is True
    assert app.toolpath_panel.top_visible is False


def test_update_tab_visibility_top_view() -> None:
    class _App:
        notebook = _Notebook("Top View")
        toolpath_panel = _ToolpathPanel()

    app = _App()

    main_tabs.update_tab_visibility(app)

    assert app.toolpath_panel.visible is False
    assert app.toolpath_panel.top_visible is True


def test_on_tab_changed_logs_when_enabled(monkeypatch) -> None:
    class _LogVar:
        def get(self):
            return True

    class _App:
        gui_logging_enabled = _LogVar()
        notebook = _Notebook("G-code")

        def __init__(self) -> None:
            self.toolpath_panel = _ToolpathPanel()
            self.log_calls = []
            self.streaming_controller = type(
                "Log", (), {"log": lambda _self, msg: self.log_calls.append(msg)}
            )()

    app = _App()
    monkeypatch.setattr(main_tabs.time, "strftime", lambda _fmt: "12:34:56")
    event = type("Event", (), {"widget": app.notebook})()

    main_tabs.on_tab_changed(app, event)

    assert app.log_calls == ["[12:34:56] Tab: G-code"]
