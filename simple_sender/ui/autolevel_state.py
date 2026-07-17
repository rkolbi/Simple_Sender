"""Fail-closed ownership helpers for active Auto-Level height maps."""

from __future__ import annotations

import logging
from typing import Any

from simple_sender.types import AutoLevelMapProvenance
from simple_sender.utils.log_suppressed import log_suppressed_exception


logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def clear_active_auto_level_map(app: Any, reason: str = "") -> None:
    """Clear active map data and notify the open dialog, if any."""
    reason_text = str(reason or "Auto-Level map ownership is no longer current.")
    provenance = getattr(app, "_auto_level_map_provenance", None)
    discard = getattr(getattr(app, "grbl", None), "discard_auto_level_map", None)
    if isinstance(provenance, AutoLevelMapProvenance) and callable(discard):
        try:
            discard(provenance, reason_text)
        except Exception as exc:
            log_suppressed_exception(
                logger,
                "Failed retiring worker-owned Auto-Level map provenance",
                exc,
                suppressed=_logged_suppressed,
            )
    app._auto_level_grid = None
    app._auto_level_height_map = None
    app._auto_level_bounds = None
    app._auto_level_map_provenance = None
    controller = getattr(app, "_auto_level_dialog_controller", None)
    callback = getattr(controller, "handle_active_map_invalidated", None)
    if callable(callback):
        try:
            callback(reason_text)
        except Exception as exc:
            log_suppressed_exception(
                logger,
                "Failed projecting Auto-Level map invalidation into the dialog",
                exc,
                suppressed=_logged_suppressed,
            )


def clear_active_auto_level_map_if_owned(
    app: Any,
    provenance: AutoLevelMapProvenance,
    reason: str = "",
) -> bool:
    """Clear only the exact active map owned by an invalidation event."""
    if getattr(app, "_auto_level_map_provenance", None) is not provenance:
        return False
    clear_active_auto_level_map(app, reason)
    return True


def active_auto_level_map_is_current(app: Any) -> bool:
    """Synchronously reject a map whose exact worker provenance is stale."""
    height_map = getattr(app, "_auto_level_height_map", None)
    provenance = getattr(app, "_auto_level_map_provenance", None)
    if height_map is None or not isinstance(provenance, AutoLevelMapProvenance):
        if height_map is not None:
            clear_active_auto_level_map(
                app,
                "Loaded height map has no current controller provenance.",
            )
        return False
    checker = getattr(
        getattr(app, "grbl", None), "auto_level_map_provenance_current", None
    )
    try:
        current = bool(checker(provenance)) if callable(checker) else False
    except Exception:
        current = False
    if not current:
        clear_active_auto_level_map(
            app,
            "Auto-Level height-map provenance became stale.",
        )
        return False
    return True


__all__ = [
    "active_auto_level_map_is_current",
    "clear_active_auto_level_map",
    "clear_active_auto_level_map_if_owned",
]
