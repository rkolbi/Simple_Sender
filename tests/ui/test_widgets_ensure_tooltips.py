from simple_sender.ui import widgets_tooltips


class _FakeWidget:
    def __init__(self, cls: str, text: str = "", children=None) -> None:
        self._cls = cls
        self._text = text
        self._children = list(children or [])

    def winfo_children(self):
        return list(self._children)

    def winfo_class(self) -> str:
        return self._cls

    def cget(self, key: str) -> str:
        if key == "text":
            return self._text
        raise KeyError(key)


def test_ensure_tooltips_only_processes_tooltip_candidates(monkeypatch) -> None:
    plain_frame = _FakeWidget("TFrame")
    button = _FakeWidget("TButton", text="Run")
    custom = _FakeWidget("Custom")
    custom._tooltip_text = "Custom hint"
    root = _FakeWidget("Root", children=[plain_frame, button, custom])

    calls: list[tuple[str, str]] = []

    def _track_apply(widget, text: str):
        calls.append((widget.winfo_class(), text))
        widget._tooltip = text
        return text

    monkeypatch.setattr(widgets_tooltips, "apply_tooltip", _track_apply)

    widgets_tooltips.ensure_tooltips(root)

    assert ("TButton", "Click to run.") in calls
    assert ("Custom", "Custom hint") in calls
    assert all(widget_cls != "TFrame" for widget_cls, _text in calls)
