"""Bounded setup-state service helpers."""

import math
from typing import Any, Callable

from simple_sender.tool_measurement import CURRENT_TOOL_REFERENCE_FORMAT

_UNAVAILABLE = object()
_ToolReferenceState = tuple[object, object]
_INVALID_TOOL_REF_TEXT = frozenset(
    {
        "",
        "none",
        "null",
        "unknown",
        "unset",
        "uninitialized",
        "n/a",
        "na",
        "--",
        "---",
        "nan",
    }
)


class JobSetupService:
    """Bounded business logic for setup-state validation and invalidation."""

    def __init__(self, *, log_suppressed: Callable[[str, BaseException], None]) -> None:
        self._log_suppressed = log_suppressed

    def has_valid_setup_state(self, app: Any) -> bool:
        trust_getter = getattr(getattr(app, "grbl", None), "machine_trust_state", None)
        trust = trust_getter() if callable(trust_getter) else None
        if trust is not None and not bool(trust.setup_tool_reference):
            return False
        state = self._read_tool_reference_state_from_macro_state(app, blocking=False)
        if not isinstance(state, tuple) or len(state) != 2:
            return False
        tool_reference, tool_reference_format = state
        if self._coerce_tool_reference_number(tool_reference) is None:
            return False
        return self._tool_reference_format_is_current(tool_reference_format)

    def invalidate_setup_state(self, app: Any) -> None:
        self._clear_tool_reference_macro_state(app)
        try:
            setattr(app, "_tool_reference_last", None)
        except Exception:
            pass
        try:
            tool_reference_var = getattr(app, "tool_reference_var", None)
            setter = getattr(tool_reference_var, "set", None)
            if callable(setter):
                setter("")
        except Exception as exc:
            self._log_suppressed("Failed clearing tool_reference_var text", exc)

    def _acquire_lock(self, lock: Any, *, blocking: bool) -> bool:
        acquire = getattr(lock, "acquire", None)
        if not callable(acquire):
            return False
        try:
            return bool(acquire(blocking=blocking))
        except TypeError:
            try:
                return bool(acquire(blocking))
            except TypeError:
                try:
                    return bool(acquire())
                except Exception:
                    return False
        except Exception:
            return False

    def _release_lock(self, lock: Any) -> None:
        release = getattr(lock, "release", None)
        if not callable(release):
            return
        try:
            release()
        except Exception as exc:
            self._log_suppressed("Failed releasing macro vars lock", exc)

    def _read_tool_reference_state_from_macro_state(
        self,
        app: Any,
        *,
        blocking: bool,
    ) -> _ToolReferenceState | object:
        macro_executor = getattr(app, "macro_executor", None)
        lock = getattr(macro_executor, "_macro_vars_lock", None)
        macro_vars = getattr(macro_executor, "_macro_vars", None)
        if (
            lock is not None
            and isinstance(macro_vars, dict)
            and hasattr(lock, "acquire")
            and hasattr(lock, "release")
        ):
            acquired = self._acquire_lock(lock, blocking=blocking)
            if not acquired:
                return _UNAVAILABLE
            try:
                macro_ns = macro_vars.get("macro")
                state = getattr(macro_ns, "state", None)
                if state is None:
                    return (None, None)
                return (
                    getattr(state, "TOOL_REFERENCE", None),
                    getattr(state, "TOOL_REFERENCE_FORMAT", None),
                )
            except Exception:
                return _UNAVAILABLE
            finally:
                self._release_lock(lock)
        if macro_executor is not None and hasattr(macro_executor, "macro_vars"):
            try:
                with macro_executor.macro_vars() as macro_vars_ctx:
                    if not isinstance(macro_vars_ctx, dict):
                        return _UNAVAILABLE
                    macro_ns = macro_vars_ctx.get("macro")
                    state = getattr(macro_ns, "state", None)
                    if state is None:
                        return (None, None)
                    return (
                        getattr(state, "TOOL_REFERENCE", None),
                        getattr(state, "TOOL_REFERENCE_FORMAT", None),
                    )
            except Exception:
                return _UNAVAILABLE
        return _UNAVAILABLE

    def _clear_tool_reference_macro_state(self, app: Any) -> None:
        macro_executor = getattr(app, "macro_executor", None)
        if macro_executor is None:
            return
        lock = getattr(macro_executor, "_macro_vars_lock", None)
        macro_vars = getattr(macro_executor, "_macro_vars", None)
        if (
            lock is not None
            and isinstance(macro_vars, dict)
            and hasattr(lock, "acquire")
            and hasattr(lock, "release")
        ):
            acquired = self._acquire_lock(lock, blocking=True)
            if not acquired:
                self._log_suppressed(
                    "Failed clearing TOOL_REFERENCE from macro state",
                    RuntimeError("macro vars lock acquire returned false"),
                )
                return
            try:
                macro_ns = macro_vars.get("macro")
                state = getattr(macro_ns, "state", None)
                if state is not None:
                    setattr(state, "TOOL_REFERENCE", None)
            except Exception as exc:
                self._log_suppressed("Failed clearing TOOL_REFERENCE from macro state", exc)
            finally:
                self._release_lock(lock)
            return
        if hasattr(macro_executor, "macro_vars"):
            try:
                with macro_executor.macro_vars() as macro_vars_ctx:
                    if not isinstance(macro_vars_ctx, dict):
                        return
                    macro_ns = macro_vars_ctx.get("macro")
                    state = getattr(macro_ns, "state", None)
                    if state is not None:
                        setattr(state, "TOOL_REFERENCE", None)
            except Exception as exc:
                self._log_suppressed("Failed clearing TOOL_REFERENCE via macro_vars context", exc)

    def _coerce_tool_reference_number(self, value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            try:
                number = float(value)
            except Exception:
                return None
            if not math.isfinite(number):
                return None
            return number
        text = str(value).strip()
        if not text:
            return None
        if text.lower().startswith("tool ref"):
            _prefix, _sep, candidate = text.partition(":")
            text = candidate.strip()
            if not text:
                return None
        if text.lower() in _INVALID_TOOL_REF_TEXT:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number

    def _tool_reference_format_is_current(self, value: object) -> bool:
        text = str(value or "").strip()
        return bool(text == CURRENT_TOOL_REFERENCE_FORMAT)
