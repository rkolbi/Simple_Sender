import pytest

tk = pytest.importorskip("tkinter")

from tkinter import ttk

from simple_sender.ui import checklists_tab

pytestmark = pytest.mark.ui


def _walk_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk_widgets(child)


def _find_button_by_text(widget, text: str):
    for child in _walk_widgets(widget):
        if isinstance(child, ttk.Button) and child.cget("text") == text:
            return child
    return None


def test_build_checklists_tab_only_builds_checklist_content(tk_root, monkeypatch) -> None:
    notebook = ttk.Notebook(tk_root)
    app = type("App", (), {})()
    fake_path = "checklist-run.chk"

    monkeypatch.setattr(checklists_tab, "discover_checklist_files", lambda _app: [fake_path])
    monkeypatch.setattr(checklists_tab, "format_checklist_title", lambda _path: "Run")
    monkeypatch.setattr(checklists_tab, "load_checklist_items", lambda _path: ["Step 1", "Step 2"])

    checklists_tab.build_checklists_tab(app, notebook)

    assert fake_path in app._checklist_vars
    assert len(app._checklist_vars[fake_path]) == 2
    assert app._checklist_collapsed[fake_path] is False
    assert not hasattr(app, "btn_preflight_check")
    assert not hasattr(app, "all_stop_combo")


def test_checklist_title_toggle_collapses_and_expands(tk_root, monkeypatch) -> None:
    notebook = ttk.Notebook(tk_root)
    app = type("App", (), {})()
    fake_path = "checklist-run.chk"

    monkeypatch.setattr(checklists_tab, "discover_checklist_files", lambda _app: [fake_path])
    monkeypatch.setattr(checklists_tab, "format_checklist_title", lambda _path: "Run")
    monkeypatch.setattr(checklists_tab, "load_checklist_items", lambda _path: ["Step 1"])

    tab = checklists_tab.build_checklists_tab(app, notebook)
    btn = _find_button_by_text(tab, "[-] Run")
    assert btn is not None
    assert app._checklist_collapsed[fake_path] is False

    btn.invoke()
    assert app._checklist_collapsed[fake_path] is True
    assert btn.cget("text") == "[+] Run"

    btn.invoke()
    assert app._checklist_collapsed[fake_path] is False
    assert btn.cget("text") == "[-] Run"


def test_checklist_title_toggles_are_independent(tk_root, monkeypatch) -> None:
    notebook = ttk.Notebook(tk_root)
    app = type("App", (), {})()
    first_path = "checklist-run.chk"
    second_path = "checklist-release.chk"

    monkeypatch.setattr(
        checklists_tab,
        "discover_checklist_files",
        lambda _app: [first_path, second_path],
    )
    monkeypatch.setattr(
        checklists_tab,
        "format_checklist_title",
        lambda path: "Run" if path == first_path else "Release",
    )
    monkeypatch.setattr(checklists_tab, "load_checklist_items", lambda _path: ["Step 1"])

    tab = checklists_tab.build_checklists_tab(app, notebook)
    run_btn = _find_button_by_text(tab, "[-] Run")
    release_btn = _find_button_by_text(tab, "[-] Release")
    assert run_btn is not None
    assert release_btn is not None

    run_btn.invoke()
    assert run_btn.cget("text") == "[+] Run"
    assert release_btn.cget("text") == "[-] Release"
    assert app._checklist_collapsed[first_path] is True
    assert app._checklist_collapsed[second_path] is False
