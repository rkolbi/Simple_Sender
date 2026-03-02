import queue

import pytest


@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("tkinter Tcl/Tk not available")
    yield root
    root.destroy()


@pytest.fixture
def ui_queue():
    return queue.Queue()


@pytest.fixture
def dummy_serial():
    class _DummySerial:
        def __init__(self) -> None:
            self.is_open = True
            self.writes = []

        def write(self, data: bytes) -> int:
            self.writes.append(data)
            return len(data)

        def read(self, _size: int = 1) -> bytes:
            return b""

        def close(self) -> None:
            self.is_open = False

    return _DummySerial()


@pytest.fixture
def connected_worker(ui_queue, dummy_serial):
    from simple_sender.grbl_worker import GrblWorker

    worker = GrblWorker(ui_queue)
    worker.ser = dummy_serial
    return worker


@pytest.fixture
def macro_app_factory():
    class _Bool:
        def __init__(self, value: bool) -> None:
            self._value = value

        def get(self):
            return self._value

    class _Grbl:
        def is_connected(self):
            return False

        def is_streaming(self):
            return False

        def send_realtime(self, _cmd):
            return None

        def send_immediate(self, _cmd):
            return None

        def wait_for_manual_completion(self, timeout_s: float = 0.0):
            return True

    def factory(allow_python: bool):
        class _App:
            def __init__(self, allow: bool) -> None:
                self.ui_q = queue.Queue()
                self.grbl = _Grbl()
                self.macros_allow_python = _Bool(allow)

        return _App(allow_python)

    return factory
