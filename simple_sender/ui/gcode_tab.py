from tkinter import ttk

from simple_sender.ui.gcode_viewer import GcodeViewer


def build_gcode_tab(app, notebook):
    nb = notebook
    # Gcode tab
    gtab = ttk.Frame(nb, padding=6)
    nb.add(gtab, text="G-code")
    stats_row = ttk.Frame(gtab)
    stats_row.pack(fill="x", pady=(0, 6))
    app.gcode_stats_label = ttk.Label(stats_row, textvariable=app.gcode_stats_var, anchor="w")
    app.gcode_stats_label.pack(side="left", fill="x", expand=True)
    app.gview = GcodeViewer(gtab)  # Using refactored GcodeViewer
    app.gview.pack(fill="both", expand=True)

