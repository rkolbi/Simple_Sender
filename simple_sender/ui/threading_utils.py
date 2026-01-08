import queue
import threading


def call_on_ui_thread(app, func, *args, timeout: float | None = 5.0, **kwargs):
    if threading.current_thread() is threading.main_thread():
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            app._log_exception("UI action failed", exc)
            return None
    result_q: queue.Queue = queue.Queue()
    app.ui_q.put(("ui_call", func, args, kwargs, result_q))
    try:
        if timeout is None:
            while True:
                try:
                    ok, value = result_q.get(timeout=0.2)
                    break
                except queue.Empty:
                    if app._closing:
                        app.ui_q.put(("log", "[ui] Action canceled (closing)."))
                        return None
        else:
            ok, value = result_q.get(timeout=timeout)
    except queue.Empty:
        app.ui_q.put(("log", "[ui] Action timed out."))
        return None
    if ok:
        return value
    app.ui_q.put(("log", f"[ui] Action failed: {value}"))
    return None


def post_ui_thread(app, func, *args, **kwargs):
    app.ui_q.put(("ui_post", func, args, kwargs))
