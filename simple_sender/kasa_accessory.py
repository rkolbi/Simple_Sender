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

"""Optional Kasa accessory control helpers and background command routing."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from simple_sender.utils.constants import KASA_TASK_QUEUE_MAXSIZE

logger = logging.getLogger(__name__)
_KASA_COMMAND_RETRY_MAX_ATTEMPTS = 3
_KASA_COMMAND_RETRY_BASE_DELAY_S = 0.2
_KASA_COMMAND_RETRY_MAX_DELAY_S = 1.0
_KASA_DUPLICATE_REQUEST_WINDOW_S = 1.0


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    identifier: str
    label: str
    ip: str
    model: str
    outlet_count: int


@dataclass(frozen=True, slots=True)
class DeviceHandle:
    identifier: str
    ip: str
    device_id: str | None = None


@dataclass(frozen=True, slots=True)
class OutletInfo:
    outlet_id: int
    name: str


@dataclass(frozen=True, slots=True)
class OutletCommandResult:
    outlet_id: int
    on: bool
    success: bool
    source: str
    error: str | None = None


class KasaController(Protocol):
    def discover(self) -> list[DeviceInfo]:
        raise NotImplementedError

    def connect(self, device_identifier: str) -> DeviceHandle:
        raise NotImplementedError

    def list_outlets(self, device_handle: DeviceHandle) -> list[OutletInfo]:
        raise NotImplementedError

    def set_outlet_state(self, device_handle: DeviceHandle, outlet_id: int, on: bool) -> None:
        raise NotImplementedError


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _split_identifier(identifier: str) -> tuple[str | None, str]:
    text = str(identifier or "").strip()
    if not text:
        return None, ""
    if "@" not in text:
        return None, text
    dev_id_raw, _, host = text.partition("@")
    dev_id: str | None = dev_id_raw.strip() or None
    host = host.strip()
    return dev_id, host


def _build_identifier(host: str, device_id: str | None) -> str:
    host_text = str(host or "").strip()
    id_text = str(device_id or "").strip()
    if id_text:
        return f"{id_text}@{host_text}"
    return host_text


def _coerce_outlet_id(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return int(default)
    if parsed not in (1, 2):
        return int(default)
    return parsed


def validate_outlet_mapping(
    *,
    vacuum_enabled: bool,
    vacuum_outlet: int,
    light_enabled: bool,
    light_outlet: int,
) -> tuple[bool, str | None]:
    if vacuum_enabled and light_enabled and int(vacuum_outlet) == int(light_outlet):
        return False, "Vacuum and Spindle Light cannot use the same outlet."
    return True, None


class SpindleCommandDetector:
    _PAREN_COMMENT_RE = re.compile(r"\([^)]*\)")
    _SPINDLE_CODE_RE = re.compile(r"(?<![A-Z0-9])M0*(?P<code>[345])(?![0-9])", re.IGNORECASE)

    @classmethod
    def detect_state_change(cls, line: str) -> bool | None:
        text = str(line or "")
        if not text:
            return None
        if ";" in text:
            text = text.split(";", 1)[0]
        text = cls._PAREN_COMMENT_RE.sub("", text)
        match = cls._SPINDLE_CODE_RE.search(text)
        if match is None:
            return None
        code = match.group("code")
        if code in ("3", "4"):
            return True
        if code == "5":
            return False
        return None


class PythonKasaController:
    """Runtime adapter for the optional ``python-kasa`` dependency."""

    _DEFAULT_REQUEST_TIMEOUT_S = 15.0

    def __init__(self, *, request_timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S) -> None:
        self._import_error: Exception | None = None
        self._import_attempted = False
        self._kasa_discover = None
        try:
            timeout = float(request_timeout_s)
        except (TypeError, ValueError):
            timeout = self._DEFAULT_REQUEST_TIMEOUT_S
        if timeout <= 0:
            timeout = self._DEFAULT_REQUEST_TIMEOUT_S
        self._request_timeout_s = timeout

    def _ensure_imported(self) -> None:
        if self._import_attempted:
            return
        self._import_attempted = True
        try:
            # ``python-kasa`` is optional and supported installs may not ship
            # usable inline typing metadata.
            from kasa import Discover  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised when dependency missing
            self._import_error = exc
            self._kasa_discover = None
        else:
            self._kasa_discover = Discover

    def _require_kasa(self) -> Any:
        self._ensure_imported()
        if self._kasa_discover is None:
            detail = str(self._import_error) if self._import_error is not None else "not installed"
            raise RuntimeError(f"python-kasa dependency unavailable: {detail}")
        return self._kasa_discover

    def _run_coro(self, coro):
        timeout_s = self._request_timeout_s

        async def _run_with_timeout():
            return await asyncio.wait_for(coro, timeout=timeout_s)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(_run_with_timeout())
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"Kasa operation timed out after {timeout_s:.1f}s.") from exc

        result_q: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def _runner() -> None:
            try:
                result_q.put((True, asyncio.run(_run_with_timeout())))
            except Exception as exc:  # pragma: no cover - defensive fallback
                result_q.put((False, exc))

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        try:
            ok, value = result_q.get(timeout=timeout_s + 1.0)
        except queue.Empty as exc:
            raise TimeoutError(f"Kasa operation timed out after {timeout_s:.1f}s.") from exc
        finally:
            thread.join(timeout=0.1)
        if ok:
            return value
        if isinstance(value, asyncio.TimeoutError):
            raise TimeoutError(f"Kasa operation timed out after {timeout_s:.1f}s.") from value
        raise value

    async def _discover_devices_async(self) -> list[DeviceInfo]:
        discover_cls = self._require_kasa()
        found = await discover_cls.discover()
        devices: list[DeviceInfo] = []
        for device in found.values():
            try:
                await device.update()
            except Exception:
                continue
            host = str(getattr(device, "host", "") or "")
            alias = str(getattr(device, "alias", "") or host or "Kasa device")
            model = str(getattr(device, "model", "") or "")
            device_id = str(getattr(device, "device_id", "") or "").strip() or None
            children = list(getattr(device, "children", []) or [])
            outlets = len(children) if children else 1
            devices.append(
                DeviceInfo(
                    identifier=_build_identifier(host, device_id),
                    label=alias,
                    ip=host,
                    model=model,
                    outlet_count=max(1, outlets),
                )
            )
        devices.sort(key=lambda dev: (dev.label.lower(), dev.ip))
        return devices

    async def _resolve_device_async(self, identifier: str):
        discover_cls = self._require_kasa()
        device_id, host = _split_identifier(identifier)
        if not host:
            raise RuntimeError("Kasa device identifier is empty.")

        candidate = None
        if _is_ip_address(host):
            candidate = await discover_cls.discover_single(host)
        if candidate is None:
            found = await discover_cls.discover()
            for device in found.values():
                dev_host = str(getattr(device, "host", "") or "")
                dev_id = str(getattr(device, "device_id", "") or "").strip()
                if dev_host == host:
                    candidate = device
                    break
                if device_id and dev_id and dev_id == device_id:
                    candidate = device
                    break
        if candidate is None:
            raise RuntimeError(f"Unable to find Kasa device '{identifier}'.")
        await candidate.update()
        return candidate

    async def _list_outlets_async(self, identifier: str) -> list[OutletInfo]:
        device = await self._resolve_device_async(identifier)
        children = list(getattr(device, "children", []) or [])
        if not children:
            return [OutletInfo(outlet_id=1, name="Outlet 1")]
        outlets: list[OutletInfo] = []
        for index, child in enumerate(children, start=1):
            alias = str(getattr(child, "alias", "") or f"Outlet {index}")
            outlets.append(OutletInfo(outlet_id=index, name=alias))
        return outlets

    async def _set_outlet_state_async(self, identifier: str, outlet_id: int, on: bool) -> None:
        device = await self._resolve_device_async(identifier)
        outlet_index = int(outlet_id) - 1
        children = list(getattr(device, "children", []) or [])
        target = None
        if children:
            if outlet_index < 0 or outlet_index >= len(children):
                raise RuntimeError(f"Outlet {outlet_id} is not available on selected Kasa device.")
            target = children[outlet_index]
        elif int(outlet_id) == 1:
            target = device
        else:
            raise RuntimeError(f"Outlet {outlet_id} is not available on selected Kasa device.")
        if on:
            await target.turn_on()
        else:
            await target.turn_off()

    def discover(self) -> list[DeviceInfo]:
        return list(self._run_coro(self._discover_devices_async()))

    def connect(self, device_identifier: str) -> DeviceHandle:
        device = self._run_coro(self._resolve_device_async(str(device_identifier or "").strip()))
        host = str(getattr(device, "host", "") or "")
        device_id = str(getattr(device, "device_id", "") or "").strip() or None
        identifier = _build_identifier(host, device_id)
        return DeviceHandle(identifier=identifier, ip=host, device_id=device_id)

    def list_outlets(self, device_handle: DeviceHandle) -> list[OutletInfo]:
        return list(self._run_coro(self._list_outlets_async(device_handle.identifier)))

    def set_outlet_state(self, device_handle: DeviceHandle, outlet_id: int, on: bool) -> None:
        self._run_coro(
            self._set_outlet_state_async(
                device_handle.identifier,
                int(outlet_id),
                bool(on),
            )
        )


class FakeKasaController:
    def __init__(
        self,
        *,
        outlet_count: int = 2,
        raise_on_set: bool = False,
        device_identifier: str = "FAKE-DEVICE@192.168.0.50",
    ) -> None:
        self.outlet_count = max(1, int(outlet_count))
        self.raise_on_set = bool(raise_on_set)
        self.device_identifier = str(device_identifier)
        self.commands: list[tuple[str, int, bool]] = []

    def discover(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                identifier=self.device_identifier,
                label="Fake Kasa Device",
                ip="192.168.0.50",
                model="FAKE",
                outlet_count=self.outlet_count,
            )
        ]

    def connect(self, device_identifier: str) -> DeviceHandle:
        if str(device_identifier) != self.device_identifier:
            raise RuntimeError(f"Unknown fake Kasa device '{device_identifier}'.")
        return DeviceHandle(identifier=self.device_identifier, ip="192.168.0.50", device_id="FAKE-DEVICE")

    def list_outlets(self, device_handle: DeviceHandle) -> list[OutletInfo]:
        if device_handle.identifier != self.device_identifier:
            raise RuntimeError(f"Unknown fake Kasa device '{device_handle.identifier}'.")
        return [OutletInfo(outlet_id=index, name=f"Outlet {index}") for index in range(1, self.outlet_count + 1)]

    def set_outlet_state(self, device_handle: DeviceHandle, outlet_id: int, on: bool) -> None:
        if device_handle.identifier != self.device_identifier:
            raise RuntimeError(f"Unknown fake Kasa device '{device_handle.identifier}'.")
        if int(outlet_id) < 1 or int(outlet_id) > self.outlet_count:
            raise RuntimeError(f"Outlet {outlet_id} unavailable for fake device.")
        if self.raise_on_set:
            raise RuntimeError("Injected Kasa failure")
        self.commands.append((device_handle.identifier, int(outlet_id), bool(on)))


@dataclass(slots=True)
class _WorkerTask:
    func: Callable[[], Any]
    on_success: Callable[[Any], None] | None
    on_error: Callable[[Exception], None] | None
    description: str


class AccessoryRouter:
    def __init__(
        self,
        *,
        controller: KasaController,
        settings_provider: Callable[[], Mapping[str, Any]],
        log: Callable[[str], None] | None = None,
        command_result_callback: Callable[[OutletCommandResult], None] | None = None,
    ) -> None:
        self._controller = controller
        self._settings_provider = settings_provider
        self._log = log
        self._command_result_callback = command_result_callback
        self._task_q: queue.Queue[_WorkerTask | None] = queue.Queue(maxsize=KASA_TASK_QUEUE_MAXSIZE)
        self._stop_evt = threading.Event()
        self._state_lock = threading.Lock()
        self._last_spindle_state: bool | None = None
        self._last_requested_by_outlet: dict[tuple[str, int], tuple[bool, float]] = {}
        self._cached_device_identifier: str | None = None
        self._cached_device_handle: DeviceHandle | None = None
        self._worker = threading.Thread(target=self._worker_loop, name="kasa-worker", daemon=True)
        self._worker.start()

    def _retry_delay_s(self, attempt_index: int) -> float:
        delay = _KASA_COMMAND_RETRY_BASE_DELAY_S * (2**max(0, int(attempt_index)))
        return min(_KASA_COMMAND_RETRY_MAX_DELAY_S, max(0.0, float(delay)))

    def _log_warning(self, message: str) -> None:
        if self._log is not None:
            try:
                self._log(message)
                return
            except Exception as exc:
                logger.debug(
                    "Failed writing Kasa message via injected logger: %s",
                    exc,
                    exc_info=exc,
                )
        logger.warning(message)

    def shutdown(self, timeout: float = 1.0) -> None:
        self._stop_evt.set()
        try:
            queue.Queue.put_nowait(self._task_q, None)
        except queue.Full:
            self._log_warning("Kasa shutdown requested while task queue is full; waiting for worker to drain.")
        self._worker.join(timeout=max(0.0, float(timeout)))

    def wait_for_idle(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self._task_q.unfinished_tasks > 0:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def reset_debounce(self) -> None:
        with self._state_lock:
            self._last_spindle_state = None
            self._last_requested_by_outlet.clear()

    def _read_settings(self) -> dict[str, Any]:
        raw = self._settings_provider() or {}
        device_identifier = str(raw.get("kasa_device_identifier", "") or "").strip()
        try:
            outlet_count = int(raw.get("kasa_outlet_count", 2))
        except Exception:
            outlet_count = 2
        outlet_count = max(1, outlet_count)
        settings = {
            "kasa_enabled": bool(raw.get("kasa_enabled", False)),
            "kasa_device_identifier": device_identifier,
            "vacuum_enabled": bool(raw.get("vacuum_enabled", False)),
            "vacuum_outlet": _coerce_outlet_id(raw.get("vacuum_outlet", 1), default=1),
            "light_enabled": bool(raw.get("light_enabled", False)),
            "light_outlet": _coerce_outlet_id(raw.get("light_outlet", 2), default=2),
            "kasa_outlet_count": outlet_count,
        }
        return settings

    def _submit_task(
        self,
        func: Callable[[], Any],
        *,
        description: str,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> bool:
        if self._stop_evt.is_set():
            err = RuntimeError(f"Kasa worker is shutting down; rejecting task '{description}'.")
            if on_error is not None:
                self._invoke_callback(on_error, err)
            else:
                self._log_warning(str(err))
            return False
        task = _WorkerTask(
            func=func,
            on_success=on_success,
            on_error=on_error,
            description=description,
        )
        try:
            self._task_q.put_nowait(task)
        except queue.Full:
            err = RuntimeError(f"Kasa task queue full; dropping task '{description}'.")
            if on_error is not None:
                self._invoke_callback(on_error, err)
            else:
                self._log_warning(str(err))
            return False
        return True

    def _invoke_callback(self, callback: Callable[..., None], *args: Any) -> None:
        try:
            callback(*args)
        except Exception as exc:  # pragma: no cover - defensive logging
            self._log_warning(f"Kasa callback failed: {exc}")

    def _worker_loop(self) -> None:
        while True:
            try:
                task = self._task_q.get(timeout=0.1)
            except queue.Empty:
                if self._stop_evt.is_set():
                    return
                continue
            try:
                if task is None:
                    return
                try:
                    result = task.func()
                except Exception as exc:
                    if task.on_error is not None:
                        self._invoke_callback(task.on_error, exc)
                    else:
                        self._log_warning(f"Kasa task '{task.description}' failed: {exc}")
                    continue
                if task.on_success is not None:
                    self._invoke_callback(task.on_success, result)
            finally:
                self._task_q.task_done()

    def _clear_device_cache(self) -> None:
        with self._state_lock:
            self._cached_device_identifier = None
            self._cached_device_handle = None

    def _device_handle_for_identifier(self, device_identifier: str) -> DeviceHandle:
        with self._state_lock:
            if (
                self._cached_device_identifier == device_identifier
                and self._cached_device_handle is not None
            ):
                return self._cached_device_handle
        handle = self._controller.connect(device_identifier)
        with self._state_lock:
            self._cached_device_identifier = device_identifier
            self._cached_device_handle = handle
        return handle

    def discover(
        self,
        *,
        on_success: Callable[[list[DeviceInfo]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._submit_task(
            lambda: self._controller.discover(),
            description="discover",
            on_success=on_success,
            on_error=on_error,
        )

    def list_outlets(
        self,
        device_identifier: str,
        *,
        on_success: Callable[[list[OutletInfo]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        identifier = str(device_identifier or "").strip()
        if not identifier:
            if on_error is not None:
                self._invoke_callback(on_error, ValueError("No Kasa device selected."))
            return

        def _task() -> list[OutletInfo]:
            handle = self._device_handle_for_identifier(identifier)
            return self._controller.list_outlets(handle)

        self._submit_task(
            _task,
            description="list_outlets",
            on_success=on_success,
            on_error=on_error,
        )

    def _emit_command_result(self, result: OutletCommandResult) -> None:
        if self._command_result_callback is None:
            return
        self._invoke_callback(self._command_result_callback, result)

    def request_outlet_state(
        self,
        outlet_id: int,
        on: bool,
        *,
        source: str,
    ) -> bool:
        settings = self._read_settings()
        if not settings["kasa_enabled"]:
            return False
        device_identifier = str(settings["kasa_device_identifier"] or "").strip()
        if not device_identifier:
            return False
        outlet_count = int(settings.get("kasa_outlet_count", 2) or 2)
        outlet_count = max(1, outlet_count)
        outlet = _coerce_outlet_id(outlet_id, default=1)
        if outlet > outlet_count:
            return False
        desired = bool(on)
        state_key = (device_identifier, outlet)
        request_ts = time.monotonic()
        with self._state_lock:
            last_request = self._last_requested_by_outlet.get(state_key)
            if last_request is not None:
                last_desired, last_ts = last_request
                if (
                    last_desired is desired
                    and (request_ts - last_ts) < _KASA_DUPLICATE_REQUEST_WINDOW_S
                ):
                    return False

        def _task() -> None:
            last_error: Exception | None = None
            max_attempts = max(1, int(_KASA_COMMAND_RETRY_MAX_ATTEMPTS))
            for attempt in range(max_attempts):
                try:
                    handle = self._device_handle_for_identifier(device_identifier)
                    self._controller.set_outlet_state(handle, outlet, desired)
                    return
                except Exception as exc:
                    last_error = exc
                    self._clear_device_cache()
                    if attempt >= (max_attempts - 1):
                        break
                    retry_delay_s = self._retry_delay_s(attempt)
                    logger.debug(
                        "Kasa outlet command retry scheduled: outlet=%d on=%s source=%s attempt=%d/%d delay=%.2fs err=%s",
                        int(outlet),
                        bool(desired),
                        str(source),
                        int(attempt + 1),
                        int(max_attempts),
                        float(retry_delay_s),
                        str(exc),
                    )
                    time.sleep(retry_delay_s)
            with self._state_lock:
                self._last_requested_by_outlet.pop(state_key, None)
            if last_error is None:
                raise RuntimeError("Kasa outlet command failed with unknown error.")
            raise last_error

        def _on_success(_: Any) -> None:
            with self._state_lock:
                self._last_requested_by_outlet[state_key] = (desired, time.monotonic())
            self._emit_command_result(
                OutletCommandResult(
                    outlet_id=outlet,
                    on=desired,
                    success=True,
                    source=source,
                    error=None,
                )
            )

        def _on_error(exc: Exception) -> None:
            with self._state_lock:
                self._last_requested_by_outlet.pop(state_key, None)
            self._log_warning(f"Kasa outlet {outlet} command failed ({source}): {exc}")
            self._emit_command_result(
                OutletCommandResult(
                    outlet_id=outlet,
                    on=desired,
                    success=False,
                    source=source,
                    error=str(exc),
                )
            )

        accepted = self._submit_task(
            _task,
            description=f"set_outlet_state:{outlet}:{desired}",
            on_success=_on_success,
            on_error=_on_error,
        )
        if not accepted:
            return False
        with self._state_lock:
            self._last_requested_by_outlet[state_key] = (desired, request_ts)
        return True

    def on_spindle_state_change(self, is_on: bool) -> None:
        desired_spindle = bool(is_on)
        with self._state_lock:
            if self._last_spindle_state is desired_spindle:
                return
            self._last_spindle_state = desired_spindle

        settings = self._read_settings()
        if not settings["kasa_enabled"]:
            return
        if not settings["kasa_device_identifier"]:
            return
        outlet_count = int(settings.get("kasa_outlet_count", 2) or 2)
        outlet_count = max(1, outlet_count)
        light_enabled = bool(settings["light_enabled"]) and outlet_count >= 2

        valid, message = validate_outlet_mapping(
            vacuum_enabled=bool(settings["vacuum_enabled"]),
            vacuum_outlet=int(settings["vacuum_outlet"]),
            light_enabled=light_enabled,
            light_outlet=int(settings["light_outlet"]),
        )
        if not valid:
            self._log_warning(f"Kasa mapping invalid; skipping spindle trigger: {message}")
            return

        if settings["vacuum_enabled"]:
            self.request_outlet_state(
                int(settings["vacuum_outlet"]),
                desired_spindle,
                source="spindle",
            )
        if light_enabled:
            self.request_outlet_state(
                int(settings["light_outlet"]),
                desired_spindle,
                source="spindle",
            )


def create_default_kasa_controller() -> KasaController:
    return PythonKasaController()


__all__ = [
    "AccessoryRouter",
    "DeviceHandle",
    "DeviceInfo",
    "FakeKasaController",
    "KasaController",
    "OutletCommandResult",
    "OutletInfo",
    "SpindleCommandDetector",
    "create_default_kasa_controller",
    "validate_outlet_mapping",
]
