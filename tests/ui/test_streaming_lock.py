from simple_sender.ui.events import set_streaming_lock


class _Widget:
    def __init__(self) -> None:
        self.state = None

    def config(self, state: str) -> None:
        self.state = state


class _App:
    def __init__(self) -> None:
        self.btn_conn = _Widget()
        self.btn_refresh = _Widget()
        self.port_combo = _Widget()
        self.btn_unit_toggle = _Widget()


def test_set_streaming_lock_updates_controls() -> None:
    app = _App()

    set_streaming_lock(app, True)

    assert app.btn_conn.state == "disabled"
    assert app.btn_refresh.state == "disabled"
    assert app.port_combo.state == "disabled"
    assert app.btn_unit_toggle.state == "disabled"

    set_streaming_lock(app, False)

    assert app.btn_conn.state == "normal"
    assert app.btn_refresh.state == "normal"
    assert app.port_combo.state == "readonly"
    assert app.btn_unit_toggle.state == "normal"


def test_set_streaming_lock_ignores_missing_controls() -> None:
    app = type("App", (), {})()

    set_streaming_lock(app, True)
