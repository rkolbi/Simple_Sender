from tkinter import messagebox


def ensure_serial_available(app, serial_available: bool, serial_error: str | None = None) -> bool:
    if serial_available:
        return True
    msg = (
        "pyserial is required to communicate with GRBL. Install pyserial (pip install pyserial) "
        "and restart the application."
    )
    if serial_error:
        msg += f"\n{serial_error}"
    messagebox.showerror("Missing dependency", msg)
    return False
