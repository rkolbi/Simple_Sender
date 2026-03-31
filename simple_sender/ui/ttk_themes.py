#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Optional (not required by the license): If you make improvements, please consider
# contributing them back upstream (e.g., via a pull request) so others can benefit.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reusable ttk theme registration for Simple Sender."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SIMPLE_SENDER_GEMINI_THEME = "simple_sender_gemini"

SIMPLE_SENDER_GEMINI_PALETTE: dict[str, str] = {
    "bg": "#11131A",
    "panel_bg": "#1A1F2B",
    "panel_raised": "#202736",
    "button_bg": "#293247",
    "button_hover": "#32405B",
    "button_pressed": "#5968E8",
    "accent": "#6E7CFF",
    "accent_hover": "#8B96FF",
    "accent_pressed": "#5968E8",
    "accent_secondary": "#5EC8FF",
    "warm_accent": "#FF9B5E",
    "fg": "#F5F7FB",
    "text_secondary": "#B8C0D4",
    "muted_fg": "#7D8598",
    "border": "#465675",
    "selection_bg": "#33477B",
    "selection_fg": "#F5F7FB",
    "progress_trough": "#161B25",
    "text_pane_bg": "#151B27",
    "text_pane_fg": "#F5F7FB",
    "text_pane_border": "#4B5F82",
    "text_pane_inactive_selection_bg": "#28385F",
    "text_pane_current_line_bg": "#394A76",
    "toggle_on": "#5EC8FF",
    "toggle_off": "#FF9B5E",
}


def _copy_palette(palette: dict[str, str]) -> dict[str, str]:
    return dict(palette)


def _gemini_theme_settings() -> dict[str, dict[str, Any]]:
    p = SIMPLE_SENDER_GEMINI_PALETTE
    return {
        ".": {
            "configure": {
                "background": p["bg"],
                "foreground": p["fg"],
                "troughcolor": p["progress_trough"],
                "selectbackground": p["selection_bg"],
                "selectforeground": p["selection_fg"],
                "focuscolor": p["accent"],
            }
        },
        "TFrame": {"configure": {"background": p["panel_bg"]}},
        "TLabelframe": {
            "configure": {
                "background": p["panel_bg"],
                "foreground": p["fg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "relief": "solid",
            }
        },
        "TLabelframe.Label": {
            "configure": {
                "background": p["panel_bg"],
                "foreground": p["text_secondary"],
            }
        },
        "TLabel": {
            "configure": {
                "background": p["panel_bg"],
                "foreground": p["fg"],
            }
        },
        "TButton": {
            "configure": {
                "background": p["button_bg"],
                "foreground": p["fg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "focuscolor": p["accent"],
                "relief": "solid",
                "padding": (10, 6),
            },
            "map": {
                "background": [
                    ("disabled", p["panel_raised"]),
                    ("pressed", p["button_pressed"]),
                    ("active", p["button_hover"]),
                ],
                "foreground": [("disabled", p["muted_fg"])],
                "bordercolor": [
                    ("focus", p["accent"]),
                    ("pressed", p["accent_pressed"]),
                    ("active", p["accent"]),
                ],
                "lightcolor": [
                    ("focus", p["accent"]),
                    ("pressed", p["accent_pressed"]),
                    ("active", p["accent"]),
                ],
                "darkcolor": [
                    ("focus", p["accent"]),
                    ("pressed", p["accent_pressed"]),
                    ("active", p["accent"]),
                ],
            },
        },
        "TCheckbutton": {
            "configure": {
                "background": p["panel_bg"],
                "foreground": p["fg"],
                "focuscolor": p["accent"],
                "padding": (6, 5),
            },
            "map": {
                "foreground": [("disabled", p["muted_fg"])],
                "background": [("active", p["panel_raised"])],
            },
        },
        "TRadiobutton": {
            "configure": {
                "background": p["panel_bg"],
                "foreground": p["fg"],
                "focuscolor": p["accent"],
                "padding": (6, 5),
            },
            "map": {
                "foreground": [("disabled", p["muted_fg"])],
                "background": [("active", p["panel_raised"])],
            },
        },
        "TEntry": {
            "configure": {
                "fieldbackground": p["panel_raised"],
                "foreground": p["fg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "selectbackground": p["selection_bg"],
                "selectforeground": p["selection_fg"],
                "padding": (8, 6),
            },
            "map": {
                "fieldbackground": [
                    ("disabled", p["panel_bg"]),
                    ("readonly", p["panel_bg"]),
                ],
                "foreground": [("disabled", p["muted_fg"])],
                "bordercolor": [("focus", p["accent"])],
                "lightcolor": [("focus", p["accent"])],
                "darkcolor": [("focus", p["accent"])],
            },
        },
        "TCombobox": {
            "configure": {
                "background": p["button_bg"],
                "fieldbackground": p["panel_raised"],
                "foreground": p["fg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "arrowcolor": p["text_secondary"],
                "selectbackground": p["selection_bg"],
                "selectforeground": p["selection_fg"],
                "padding": (8, 6),
            },
            "map": {
                "background": [
                    ("disabled", p["panel_raised"]),
                    ("readonly", p["button_bg"]),
                    ("active", p["button_hover"]),
                ],
                "fieldbackground": [
                    ("disabled", p["panel_raised"]),
                    ("readonly", p["panel_raised"]),
                ],
                "foreground": [("disabled", p["muted_fg"])],
                "bordercolor": [("focus", p["accent"]), ("active", p["accent_secondary"])],
                "lightcolor": [("focus", p["accent"]), ("active", p["accent_secondary"])],
                "darkcolor": [("focus", p["accent"]), ("active", p["accent_secondary"])],
                "arrowcolor": [
                    ("disabled", p["muted_fg"]),
                    ("focus", p["accent_secondary"]),
                    ("active", p["fg"]),
                ],
            },
        },
        "TNotebook": {
            "configure": {
                "background": p["bg"],
                "bordercolor": p["border"],
                "tabmargins": (4, 4, 4, 0),
            }
        },
        "TNotebook.Tab": {
            "configure": {
                "background": p["panel_raised"],
                "foreground": p["text_secondary"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "padding": (14, 8),
            },
            "map": {
                "background": [
                    ("selected", p["button_bg"]),
                    ("active", p["button_hover"]),
                ],
                "foreground": [
                    ("selected", p["fg"]),
                    ("active", p["fg"]),
                ],
                "bordercolor": [("selected", p["accent"]), ("active", p["accent_secondary"])],
                "lightcolor": [("selected", p["accent"]), ("active", p["accent_secondary"])],
                "darkcolor": [("selected", p["accent"]), ("active", p["accent_secondary"])],
            },
        },
        "Treeview": {
            "configure": {
                "background": p["panel_bg"],
                "fieldbackground": p["panel_bg"],
                "foreground": p["fg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "rowheight": 24,
            },
            "map": {
                "background": [("selected", p["selection_bg"])],
                "foreground": [("selected", p["selection_fg"])],
            },
        },
        "Treeview.Heading": {
            "configure": {
                "background": p["button_bg"],
                "foreground": p["fg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "padding": (8, 6),
            },
            "map": {
                "background": [
                    ("pressed", p["button_pressed"]),
                    ("active", p["button_hover"]),
                ],
                "foreground": [("disabled", p["muted_fg"])],
                "bordercolor": [("active", p["accent"])],
            },
        },
        "TScrollbar": {
            "configure": {
                "background": p["button_bg"],
                "troughcolor": p["text_pane_bg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "arrowcolor": p["text_secondary"],
                "width": 16,
                "arrowsize": 16,
                "gripcount": 0,
            },
            "map": {
                "background": [
                    ("pressed", p["button_pressed"]),
                    ("active", p["button_hover"]),
                ],
                "arrowcolor": [
                    ("disabled", p["muted_fg"]),
                    ("active", p["fg"]),
                ],
                "bordercolor": [("active", p["accent"]), ("pressed", p["accent_pressed"])],
            },
        },
        "Horizontal.TScrollbar": {
            "configure": {
                "background": p["button_bg"],
                "troughcolor": p["text_pane_bg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "arrowcolor": p["text_secondary"],
                "width": 16,
                "arrowsize": 16,
            }
        },
        "Vertical.TScrollbar": {
            "configure": {
                "background": p["button_bg"],
                "troughcolor": p["text_pane_bg"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
                "arrowcolor": p["text_secondary"],
                "width": 16,
                "arrowsize": 16,
            }
        },
        "Horizontal.TScale": {
            "configure": {
                "background": p["panel_bg"],
                "troughcolor": p["panel_raised"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
            }
        },
        "Vertical.TScale": {
            "configure": {
                "background": p["panel_bg"],
                "troughcolor": p["panel_raised"],
                "bordercolor": p["border"],
                "lightcolor": p["border"],
                "darkcolor": p["border"],
            }
        },
        "TSeparator": {"configure": {"background": p["border"]}},
    }


def register_simple_sender_themes(style) -> dict[str, dict[str, str]]:
    """Register custom ttk themes and return the palettes that were made available."""

    if style is None:
        return {}
    try:
        theme_names = set(style.theme_names())
    except Exception as exc:
        logger.debug("Failed reading ttk theme names: %s", exc, exc_info=exc)
        return {}

    settings = _gemini_theme_settings()
    palettes: dict[str, dict[str, str]] = {}
    theme_settings = getattr(style, "theme_settings", None)

    if SIMPLE_SENDER_GEMINI_THEME in theme_names:
        if callable(theme_settings):
            try:
                theme_settings(SIMPLE_SENDER_GEMINI_THEME, settings)
            except Exception as exc:
                logger.debug(
                    "Failed refreshing existing ttk theme %s: %s",
                    SIMPLE_SENDER_GEMINI_THEME,
                    exc,
                    exc_info=exc,
                )
        palettes[SIMPLE_SENDER_GEMINI_THEME] = _copy_palette(SIMPLE_SENDER_GEMINI_PALETTE)
        return palettes

    if "clam" not in theme_names:
        logger.debug(
            "Skipping %s registration because ttk theme 'clam' is unavailable",
            SIMPLE_SENDER_GEMINI_THEME,
        )
        return {}

    try:
        style.theme_create(
            SIMPLE_SENDER_GEMINI_THEME,
            parent="clam",
            settings=settings,
        )
    except Exception as exc:
        logger.debug(
            "Failed registering ttk theme %s: %s",
            SIMPLE_SENDER_GEMINI_THEME,
            exc,
            exc_info=exc,
        )
        return {}

    palettes[SIMPLE_SENDER_GEMINI_THEME] = _copy_palette(SIMPLE_SENDER_GEMINI_PALETTE)
    return palettes
