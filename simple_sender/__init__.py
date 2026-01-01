"""Simple Sender - GRBL 1.1h CNC Controller.

A minimal, reliable GRBL sender for 3-axis CNC machines with Python + Tkinter.
"""

__version__ = "1.0.0"
__author__ = "Simple Sender Team"

from .grbl_worker import GrblWorker
from .utils import Settings

__all__ = [
    "GrblWorker",
    "Settings",
]
