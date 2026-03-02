import os

from simple_sender.ui import macro_files


class _Executor:
    def __init__(self, search_dirs, mapping=None) -> None:
        self._macro_search_dirs = tuple(search_dirs)
        self._mapping = mapping or {}

    def macro_path(self, index: int):
        return self._mapping.get(int(index))


class _App:
    def __init__(self, search_dirs, mapping=None) -> None:
        self.macro_executor = _Executor(search_dirs, mapping=mapping)

    def winfo_rgb(self, color: str):
        color_map = {
            "#00ff00": (0, 65535, 0),
            "#ffffff": (65535, 65535, 65535),
            "red": (65535, 0, 0),
        }
        if color in color_map:
            return color_map[color]
        raise ValueError(color)


def test_get_writable_macro_dir_prefers_first_existing(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    app = _App((str(first), str(second)))

    assert macro_files.get_writable_macro_dir(app) == str(first)


def test_write_read_and_remove_macro_slot_roundtrip(tmp_path) -> None:
    macro_dir = tmp_path / "macros"
    macro_dir.mkdir()
    path = macro_files.write_macro_slot(
        str(macro_dir),
        3,
        name="Tool Change",
        tip="Swap tool and continue",
        color="#00ff00",
        text_color="#ffffff",
        body="G21\nM5",
    )
    app = _App((str(macro_dir),), mapping={3: path})

    name, tip, color, text_color, body, resolved = macro_files.read_macro_slot(app, 3)
    assert name == "Tool Change"
    assert tip == "Swap tool and continue"
    assert color == "#00ff00"
    assert text_color == "#ffffff"
    assert body == "G21\nM5"
    assert resolved == path

    macro_files.remove_macro_slot(str(macro_dir), 3)
    assert not os.path.exists(path)


def test_read_macro_slot_treats_line_three_as_mandatory_color_header(tmp_path) -> None:
    macro_dir = tmp_path / "macros"
    macro_dir.mkdir()
    path = macro_dir / "Macro-2"
    path.write_text("Legacy\nTip\nG21\nM5\n", encoding="utf-8")
    app = _App((str(macro_dir),), mapping={2: str(path)})

    name, tip, color, text_color, body, resolved = macro_files.read_macro_slot(app, 2)

    assert name == "Legacy"
    assert tip == "Tip"
    assert color == ""
    assert text_color == ""
    assert body == ""
    assert resolved == str(path)


def test_read_macro_slot_treats_line_four_as_mandatory_text_color_header(tmp_path) -> None:
    macro_dir = tmp_path / "macros"
    macro_dir.mkdir()
    path = macro_dir / "Macro-3"
    path.write_text("Styled\nTip\n#00ff00\nG21\nM5\n", encoding="utf-8")
    app = _App((str(macro_dir),), mapping={3: str(path)})

    name, tip, color, text_color, body, resolved = macro_files.read_macro_slot(app, 3)

    assert name == "Styled"
    assert tip == "Tip"
    assert color == "#00ff00"
    assert text_color == ""
    assert body == "M5"
    assert resolved == str(path)


def test_discover_macro_assets_dedupes_by_filename(tmp_path) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()

    (primary / "Macro-1").write_text("A\nTip\n", encoding="utf-8")
    (secondary / "Macro-1").write_text("B\nTip\n", encoding="utf-8")
    (secondary / "checklist-run.chk").write_text("Step 1\n", encoding="utf-8")

    app = _App((str(primary), str(secondary)))
    assets = macro_files.discover_macro_assets(app)
    by_name = {name: path for path, name in assets}

    assert by_name["Macro-1"] == str(primary / "Macro-1")
    assert by_name["checklist-run.chk"] == str(secondary / "checklist-run.chk")


def test_write_macro_slot_allows_text_color_without_button_color(tmp_path) -> None:
    macro_dir = tmp_path / "macros"
    macro_dir.mkdir()

    path = macro_files.write_macro_slot(
        str(macro_dir),
        1,
        name="Macro 1",
        tip="Tip",
        color="",
        text_color="#ffffff",
        body="G21",
    )
    text = (macro_dir / "Macro-1").read_text(encoding="utf-8")
    assert path == str(macro_dir / "Macro-1")
    assert text.startswith("Macro 1\nTip\n\n#ffffff\n")


def test_write_macro_slot_includes_blank_color_header_lines(tmp_path) -> None:
    macro_dir = tmp_path / "macros"
    macro_dir.mkdir()

    macro_files.write_macro_slot(
        str(macro_dir),
        2,
        name="Macro 2",
        tip="Tip",
        color="",
        text_color="",
        body="G21",
    )
    lines = (macro_dir / "Macro-2").read_text(encoding="utf-8").splitlines()
    assert lines[:5] == ["Macro 2", "Tip", "", "", "G21"]
