import pytest

pytest.importorskip("tkinter")

from simple_sender.ui.dialogs import touch_file_browser

pytestmark = pytest.mark.ui


def test_normalize_start_dir_prefers_existing_path(tmp_path) -> None:
    resolved = touch_file_browser._normalize_start_dir(str(tmp_path))
    assert resolved == str(tmp_path)


def test_apply_default_extension_adds_missing_suffix() -> None:
    out = touch_file_browser._apply_default_extension("C:/tmp/file", ".nc")
    assert out.endswith(".nc")


def test_apply_default_extension_keeps_existing_suffix() -> None:
    out = touch_file_browser._apply_default_extension("C:/tmp/file.tap", ".nc")
    assert out.endswith(".tap")


def test_ask_open_path_uses_tkfilebrowser(monkeypatch) -> None:
    calls = {}

    class _FakeBrowser:
        @staticmethod
        def askopenfilename(**kwargs):
            calls["kwargs"] = kwargs
            return "C:/tmp/job.gcode"

    monkeypatch.setattr(touch_file_browser, "_tkfilebrowser", _FakeBrowser)

    result = touch_file_browser.ask_open_path(
        object(),
        title="Open G-code",
        initialdir="C:/tmp",
        filetypes=[("G-code", "*.gcode")],
    )

    assert result == "C:/tmp/job.gcode"
    assert calls["kwargs"]["title"] == "Open G-code"


def test_ask_save_path_applies_default_extension(monkeypatch) -> None:
    class _FakeBrowser:
        @staticmethod
        def asksaveasfilename(**_kwargs):
            return "C:/tmp/export"

    monkeypatch.setattr(touch_file_browser, "_tkfilebrowser", _FakeBrowser)

    result = touch_file_browser.ask_save_path(
        object(),
        title="Save",
        initialdir="C:/tmp",
        defaultextension=".zip",
    )

    assert result == "C:/tmp/export.zip"


def test_browse_for_gcode_path_builds_expected_filter(monkeypatch) -> None:
    captured = {}

    def _fake_open(_app, *args, **kwargs):
        del args
        captured["kwargs"] = kwargs
        return "C:/tmp/a.nc"

    monkeypatch.setattr(touch_file_browser, "ask_open_path", _fake_open)

    path = touch_file_browser.browse_for_gcode_path(object(), initial_dir="C:/tmp")

    assert path == "C:/tmp/a.nc"
    assert captured["kwargs"]["title"] == "Open G-code"
    assert captured["kwargs"]["filetypes"][0][0] == "G-code"
