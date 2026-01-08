from tkinter import ttk

from simple_sender.ui.app_settings import build_app_settings_tab
from simple_sender.ui.console_panel import build_console_tab
from simple_sender.ui.gcode_tab import build_gcode_tab
from simple_sender.ui.overdrive_tab import build_overdrive_tab


def build_main_tabs(app, parent):
    # Bottom notebook: G-code + Console + Settings
    nb = ttk.Notebook(parent)
    app.notebook = nb
    nb.pack(side="top", fill="both", expand=True, pady=(10, 0))
    nb.bind("<<NotebookTabChanged>>", app._on_tab_changed)

    # Gcode tab
    build_gcode_tab(app, nb)

    # Console tab
    build_console_tab(app, nb)

    otab = ttk.Frame(nb, padding=6)
    nb.add(otab, text="Overdrive")
    build_overdrive_tab(app, otab)
    app.settings_controller.build_tabs(nb)

    # App Settings tab
    build_app_settings_tab(app, nb)

    # 3D tab
    app.toolpath_panel.build_tab(nb)
    app._update_tab_visibility(nb)

