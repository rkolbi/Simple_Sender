import pytest

pytest.importorskip("tkinter")
from tkinter import ttk

from simple_sender.ui.toolpath.toolpath_top_view import TopViewPanel
from simple_sender.ui.toolpath import toolpath_top_view as top_view_mod

pytestmark = pytest.mark.ui


def _build_panel(tk_root) -> TopViewPanel:
    host = ttk.Frame(tk_root)
    host.pack(fill="both", expand=True)
    panel = TopViewPanel(host)
    panel.pack(fill="both", expand=True)
    panel.canvas.configure(width=320, height=220)
    tk_root.update_idletasks()
    panel.canvas.winfo_width = lambda: 320
    panel.canvas.winfo_height = lambda: 220
    return panel


def _seed_scene(panel: TopViewPanel) -> None:
    panel.segments = [
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.0, "feed"),
        (1.0, 1.0, 0.0, 2.0, 0.5, 0.0, "feed"),
    ]
    panel.bounds = (0.0, 2.0, 0.0, 1.0, 0.0, 0.0)
    panel._invalidate_scene()


def test_render_skips_static_redraw_when_signature_is_unchanged(tk_root) -> None:
    panel = _build_panel(tk_root)
    _seed_scene(panel)

    delete_calls: list[tuple] = []
    original_delete = panel.canvas.delete

    def _tracked_delete(*args):
        delete_calls.append(args)
        return original_delete(*args)

    panel.canvas.delete = _tracked_delete

    panel._render()
    assert len(delete_calls) == 1

    panel._render()
    assert len(delete_calls) == 1


def test_render_redraws_after_scene_invalidation(tk_root) -> None:
    panel = _build_panel(tk_root)
    _seed_scene(panel)

    delete_calls: list[tuple] = []
    original_delete = panel.canvas.delete

    def _tracked_delete(*args):
        delete_calls.append(args)
        return original_delete(*args)

    panel.canvas.delete = _tracked_delete

    panel._render()
    baseline = len(delete_calls)

    panel._invalidate_scene()
    panel._render()
    assert len(delete_calls) == baseline + 1


def test_set_position_updates_marker_without_full_redraw(tk_root) -> None:
    panel = _build_panel(tk_root)
    _seed_scene(panel)

    delete_calls: list[tuple] = []
    original_delete = panel.canvas.delete

    def _tracked_delete(*args):
        delete_calls.append(args)
        return original_delete(*args)

    panel.canvas.delete = _tracked_delete

    panel._render()
    baseline = len(delete_calls)
    panel.set_position(0.5, 0.5, 0.0)

    assert len(delete_calls) == baseline
    assert panel._position_item is not None


def test_render_progressive_chunks_for_large_scene(tk_root, monkeypatch) -> None:
    panel = _build_panel(tk_root)
    panel.segments = [
        (float(i), 0.0, 0.0, float(i + 1), 1.0, 0.0, "feed")
        for i in range(250)
    ]
    panel.bounds = (0.0, 250.0, 0.0, 1.0, 0.0, 0.0)
    panel._invalidate_scene()

    monkeypatch.setattr(top_view_mod, "TOOLPATH_TOP_VIEW_PROGRESSIVE_RENDER_THRESHOLD", 4)
    monkeypatch.setattr(top_view_mod, "TOOLPATH_TOP_VIEW_PROGRESSIVE_CHUNK_SIZE", 3)

    after_calls: list[int] = []

    def _after(_ms, func):
        after_calls.append(1)
        func()
        return f"after-{len(after_calls)}"

    panel.after = _after
    panel._render()

    assert after_calls
    assert panel._progressive_overlay_item is not None
    overlay_text = panel.canvas.itemcget(panel._progressive_overlay_item, "text")
    assert "drawing..." not in overlay_text.lower()


def test_invalidate_scene_cancels_progressive_callback(tk_root) -> None:
    panel = _build_panel(tk_root)
    cancelled: list[str] = []
    panel.after_cancel = lambda after_id: cancelled.append(str(after_id))
    panel._progressive_render_after_id = "token-1"

    panel._invalidate_scene()

    assert cancelled == ["token-1"]
    assert panel._progressive_render_after_id is None
