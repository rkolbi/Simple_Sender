from simple_sender.ui.settings import scroll as app_settings_scroll


class _Canvas:
    def __init__(self) -> None:
        self.config = {}
        self.scroll_calls = []
        self.binds = []
        self.unbinds = []
        self.scan_mark_calls = []
        self.scan_dragto_calls = []
        self.pointer_x = 0
        self.pointer_y = 0
        self.root_x = 0
        self.root_y = 0
        self.pointer_widget = None

    def bbox(self, _tag):
        return (0, 0, 100, 200)

    def configure(self, **kwargs):
        self.config.update(kwargs)

    def yview_scroll(self, delta, units):
        self.scroll_calls.append((delta, units))

    def bind_all(self, sequence, func, add=None):
        self.binds.append((sequence, func, add))

    def unbind_all(self, sequence):
        self.unbinds.append(sequence)

    def winfo_pointerx(self):
        return self.pointer_x

    def winfo_pointery(self):
        return self.pointer_y

    def winfo_rootx(self):
        return self.root_x

    def winfo_rooty(self):
        return self.root_y

    def scan_mark(self, x, y):
        self.scan_mark_calls.append((x, y))

    def scan_dragto(self, x, y, gain=1):
        self.scan_dragto_calls.append((x, y, gain))

    def winfo_containing(self, _x, _y):
        return self.pointer_widget


class _Widget:
    def __init__(self, master=None):
        self.master = master


class _Event:
    def __init__(self, delta=None, num=None, widget=None):
        self.delta = delta
        self.num = num
        self.widget = widget


def test_update_app_settings_scrollregion_updates_canvas() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()

    app_settings_scroll.update_app_settings_scrollregion(app)

    assert app.app_settings_canvas.config["scrollregion"] == (0, 0, 100, 200)


def test_update_app_settings_scrollregion_no_canvas_noop() -> None:
    app = type("App", (), {})()

    app_settings_scroll.update_app_settings_scrollregion(app)


def test_on_app_settings_mousewheel_uses_delta() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)
    inner_child = _Widget(master=app._app_settings_inner)

    result = app_settings_scroll.on_app_settings_mousewheel(app, _Event(delta=120, widget=inner_child))

    assert result == "break"
    assert app.app_settings_canvas.scroll_calls == [(-1, "units")]


def test_on_app_settings_mousewheel_uses_button_numbers() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)
    inner_child = _Widget(master=app._app_settings_inner)

    app_settings_scroll.on_app_settings_mousewheel(app, _Event(num=4, widget=inner_child))
    app_settings_scroll.on_app_settings_mousewheel(app, _Event(num=5, widget=inner_child))

    assert app.app_settings_canvas.scroll_calls == [(-1, "units"), (1, "units")]


def test_bind_and_unbind_mousewheel() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._on_app_settings_mousewheel = lambda _event: None

    app_settings_scroll.bind_app_settings_mousewheel(app)
    app_settings_scroll.unbind_app_settings_mousewheel(app)

    assert app.app_settings_canvas.binds == [
        ("<MouseWheel>", app._on_app_settings_mousewheel, "+"),
        ("<Button-4>", app._on_app_settings_mousewheel, "+"),
        ("<Button-5>", app._on_app_settings_mousewheel, "+"),
    ]
    assert app.app_settings_canvas.unbinds == []
    assert app._app_settings_mousewheel_enabled is False


def test_on_app_settings_mousewheel_ignores_non_settings_widget() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)

    outside_parent = _Widget()
    app_settings_scroll.on_app_settings_mousewheel(
        app,
        _Event(delta=120, widget=_Widget(master=outside_parent)),
    )

    assert app.app_settings_canvas.scroll_calls == []


def test_on_app_settings_mousewheel_accumulates_small_delta() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)
    inner_child = _Widget(master=app._app_settings_inner)

    app_settings_scroll.on_app_settings_mousewheel(app, _Event(delta=60, widget=inner_child))
    app_settings_scroll.on_app_settings_mousewheel(app, _Event(delta=60, widget=inner_child))

    assert app.app_settings_canvas.scroll_calls == [(-1, "units")]


def test_touch_scroll_ignores_non_settings_widget() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)
    app.app_settings_canvas.pointer_x = 10
    app.app_settings_canvas.pointer_y = 12

    outside_parent = _Widget()
    app_settings_scroll.on_app_settings_touch_start(
        app,
        _Event(widget=_Widget(master=outside_parent)),
    )

    assert not getattr(app, "_app_settings_touch_active", False)
    assert app.app_settings_canvas.scan_mark_calls == []


def test_touch_scroll_disallowed_start_clears_stale_active_state() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)
    app._app_settings_touch_active = True
    app._app_settings_touch_moved = True

    outside_parent = _Widget()
    app_settings_scroll.on_app_settings_touch_start(
        app,
        _Event(widget=_Widget(master=outside_parent)),
    )

    assert app._app_settings_touch_active is False
    assert app._app_settings_touch_moved is False


def test_touch_scroll_moves_when_started_in_settings_content() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)
    inner_child = _Widget(master=app._app_settings_inner)

    app.app_settings_canvas.pointer_x = 10
    app.app_settings_canvas.pointer_y = 10
    app_settings_scroll.on_app_settings_touch_start(app, _Event(widget=inner_child))

    app.app_settings_canvas.pointer_x = 24
    app.app_settings_canvas.pointer_y = 26
    result = app_settings_scroll.on_app_settings_touch_move(app, _Event(widget=inner_child))

    assert result == "break"
    assert app.app_settings_canvas.scan_mark_calls == [(10, 10)]
    assert app.app_settings_canvas.scan_dragto_calls == [(24, 26, 1)]


def test_touch_scroll_move_over_scrollbar_releases_touch_scroll() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)
    inner_child = _Widget(master=app._app_settings_inner)
    scrollbar = _Widget(master=app.app_settings_canvas)
    scrollbar.winfo_class = lambda: "TScrollbar"

    app.app_settings_canvas.pointer_x = 10
    app.app_settings_canvas.pointer_y = 10
    app_settings_scroll.on_app_settings_touch_start(app, _Event(widget=inner_child))
    assert app._app_settings_touch_active is True

    app.app_settings_canvas.pointer_x = 24
    app.app_settings_canvas.pointer_y = 26
    result = app_settings_scroll.on_app_settings_touch_move(app, _Event(widget=scrollbar))

    assert result is None
    assert app._app_settings_touch_active is False
    assert app._app_settings_touch_moved is False
    assert app.app_settings_canvas.scan_dragto_calls == []


def test_touch_scroll_move_releases_when_pointer_is_over_scrollbar() -> None:
    app = type("App", (), {})()
    app.app_settings_canvas = _Canvas()
    app._app_settings_inner = _Widget(master=app.app_settings_canvas)
    inner_child = _Widget(master=app._app_settings_inner)
    scrollbar = _Widget(master=app.app_settings_canvas)
    scrollbar.winfo_class = lambda: "TScrollbar"

    app.app_settings_canvas.pointer_x = 10
    app.app_settings_canvas.pointer_y = 10
    app_settings_scroll.on_app_settings_touch_start(app, _Event(widget=inner_child))
    assert app._app_settings_touch_active is True

    app.app_settings_canvas.pointer_widget = scrollbar
    app.app_settings_canvas.pointer_x = 30
    app.app_settings_canvas.pointer_y = 40
    result = app_settings_scroll.on_app_settings_touch_move(app, _Event(widget=inner_child))

    assert result is None
    assert app._app_settings_touch_active is False
    assert app._app_settings_touch_moved is False
    assert app.app_settings_canvas.scan_dragto_calls == []
