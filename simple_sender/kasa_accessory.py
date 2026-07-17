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
import errno
import ipaddress
import logging
import platform
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from simple_sender.utils.constants import KASA_TASK_QUEUE_MAXSIZE

logger = logging.getLogger(__name__)
_KASA_COMMAND_RETRY_MAX_ATTEMPTS = 3
_KASA_COMMAND_RETRY_BASE_DELAY_S = 0.2
_KASA_COMMAND_RETRY_MAX_DELAY_S = 1.0
_KASA_DUPLICATE_REQUEST_WINDOW_S = 1.0
_KASA_CONNECTIVITY_TIMEOUT_S = 0.75
_KASA_CONNECTIVITY_MAX_TEXT = 500
_PROVISIONAL_DEVICE_IDENTITY = "provisional:configured-device"
_ROUTER_ID_LOCK = threading.Lock()
_NEXT_ROUTER_INSTANCE_ID = 0
_NEXT_ROUTER_SESSION_ID = 0

OutletStateKey = tuple[str, int]


def _next_router_instance_id() -> int:
    global _NEXT_ROUTER_INSTANCE_ID
    with _ROUTER_ID_LOCK:
        _NEXT_ROUTER_INSTANCE_ID += 1
        return int(_NEXT_ROUTER_INSTANCE_ID)


def _next_router_session_id() -> int:
    global _NEXT_ROUTER_SESSION_ID
    with _ROUTER_ID_LOCK:
        _NEXT_ROUTER_SESSION_ID += 1
        return int(_NEXT_ROUTER_SESSION_ID)


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
    device_identifier: str | None = None
    device_ip: str | None = None
    line_index: int | None = None
    attempts: int | None = None
    max_attempts: int | None = None
    timeout_s: float | None = None
    elapsed_s: float | None = None
    final_state: bool | None = None
    failure_kind: str | None = None
    connectivity: Mapping[str, str] | None = None
    connection_generation: int | None = None
    stream_epoch: int | None = None
    recovery_epoch: int | None = None
    source_id: int | None = None
    accessory_command_id: int | None = None
    safety_priority: bool = False
    superseded: bool = False


@dataclass(frozen=True, slots=True)
class _AccessoryCommandIdentity:
    router_instance_id: int
    router_session_id: int
    connection_generation: int
    stream_epoch: int
    recovery_epoch: int
    source_id: int
    accessory_command_id: int
    source: str
    desired_on: bool
    safety_priority: bool


@dataclass(frozen=True, slots=True)
class _DispatchCommitment:
    identity: _AccessoryCommandIdentity
    device_identifier: str


@dataclass(slots=True)
class _SharedOutletDispatchEntry:
    state_key: OutletStateKey
    barrier: threading.Lock = field(default_factory=threading.Lock)
    idle_event: threading.Event = field(default_factory=threading.Event)
    active_operations: int = 0
    active_commitment: _DispatchCommitment | None = None
    registered_router_instances: set[int] = field(default_factory=set)
    recovery_off_status: str | None = None
    recovery_off_identity: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        self.idle_event.set()


class _PhysicalOutletDispatchRegistry:
    """Process-level dispatch ordering for one physical outlet."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[OutletStateKey, _SharedOutletDispatchEntry] = {}

    def entry_for(self, state_key: OutletStateKey) -> _SharedOutletDispatchEntry:
        key = (str(state_key[0]), int(state_key[1]))
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _SharedOutletDispatchEntry(state_key=key)
                self._entries[key] = entry
            return entry

    def register_router_instance(
        self,
        entry: _SharedOutletDispatchEntry,
        router_instance_id: int,
    ) -> None:
        with self._lock:
            key = (str(entry.state_key[0]), int(entry.state_key[1]))
            current = self._entries.get(key)
            if current is not entry:
                if current is None:
                    self._entries[key] = entry
                else:
                    entry = current
            entry.registered_router_instances.add(int(router_instance_id))

    def unregister_router_instance(self, router_instance_id: int) -> None:
        with self._lock:
            for key, entry in list(self._entries.items()):
                entry.registered_router_instances.discard(int(router_instance_id))
                self._maybe_cleanup_locked(key, entry)

    def _maybe_cleanup_locked(
        self,
        key: OutletStateKey,
        entry: _SharedOutletDispatchEntry,
    ) -> None:
        if int(entry.active_operations) > 0:
            return
        if entry.active_commitment is not None:
            return
        if entry.registered_router_instances:
            return
        if entry.recovery_off_status is not None:
            return
        if self._entries.get(key) is entry:
            self._entries.pop(key, None)

    def recovery_off_status(self, state_key: OutletStateKey) -> str | None:
        key = (str(state_key[0]), int(state_key[1]))
        with self._lock:
            entry = self._entries.get(key)
            return None if entry is None else entry.recovery_off_status

    def recovery_off_identity(
        self,
        state_key: OutletStateKey,
    ) -> tuple[int, int] | None:
        key = (str(state_key[0]), int(state_key[1]))
        with self._lock:
            entry = self._entries.get(key)
            return None if entry is None else entry.recovery_off_identity

    def recovery_off_status_snapshot(self) -> dict[OutletStateKey, str]:
        with self._lock:
            return {
                key: str(entry.recovery_off_status)
                for key, entry in self._entries.items()
                if entry.recovery_off_status is not None
            }

    def set_recovery_off_status(
        self,
        state_key: OutletStateKey,
        *,
        status: str,
        recovery_identity: tuple[int, int],
    ) -> None:
        key = (str(state_key[0]), int(state_key[1]))
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _SharedOutletDispatchEntry(state_key=key)
                self._entries[key] = entry
            entry.recovery_off_status = str(status)
            entry.recovery_off_identity = (
                int(recovery_identity[0]),
                int(recovery_identity[1]),
            )

    def retire_recovery_off_status(
        self,
        state_key: OutletStateKey,
        *,
        recovery_identity: tuple[int, int],
    ) -> bool:
        key = (str(state_key[0]), int(state_key[1]))
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            if entry.recovery_off_status != "confirmed":
                return False
            if entry.recovery_off_identity != (
                int(recovery_identity[0]),
                int(recovery_identity[1]),
            ):
                return False
            if int(entry.active_operations) > 0:
                return False
            entry.recovery_off_status = None
            entry.recovery_off_identity = None
            self._maybe_cleanup_locked(key, entry)
            return True

    def begin_committed(
        self,
        entry: _SharedOutletDispatchEntry,
        commitment: _DispatchCommitment,
    ) -> None:
        with self._lock:
            entry.active_operations += 1
            entry.active_commitment = commitment
            entry.idle_event.clear()

    def finish_committed(
        self,
        entry: _SharedOutletDispatchEntry,
        commitment: _DispatchCommitment,
    ) -> None:
        with self._lock:
            entry.active_operations = max(0, int(entry.active_operations) - 1)
            if entry.active_commitment == commitment:
                entry.active_commitment = None
            if entry.active_operations <= 0:
                entry.idle_event.set()
                self._maybe_cleanup_locked(entry.state_key, entry)

    def entries_for_router_instance_session(
        self,
        router_instance_id: int,
        router_session_id: int,
    ) -> list[_SharedOutletDispatchEntry]:
        with self._lock:
            return [
                entry
                for entry in self._entries.values()
                if entry.active_commitment is not None
                and int(entry.active_commitment.identity.router_instance_id)
                == int(router_instance_id)
                and int(entry.active_commitment.identity.router_session_id)
                == int(router_session_id)
                and int(entry.active_operations) > 0
            ]

    def wait_for_router_instance_session(
        self,
        *,
        router_instance_id: int,
        router_session_id: int,
        timeout: float,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            entries = self.entries_for_router_instance_session(
                router_instance_id,
                router_session_id,
            )
            if not entries:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            for entry in entries:
                entry.idle_event.wait(min(remaining, 0.05))

    def active_count(self, state_key: OutletStateKey) -> int:
        key = (str(state_key[0]), int(state_key[1]))
        with self._lock:
            entry = self._entries.get(key)
            return 0 if entry is None else int(entry.active_operations)

    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def has_entry(self, state_key: OutletStateKey) -> bool:
        key = (str(state_key[0]), int(state_key[1]))
        with self._lock:
            return key in self._entries

    def clear_for_tests(self) -> None:
        with self._lock:
            self._entries.clear()


_PHYSICAL_OUTLET_DISPATCH_REGISTRY = _PhysicalOutletDispatchRegistry()


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


def _format_mapping(mapping: Mapping[str, str] | None) -> str:
    if not mapping:
        return ""
    parts: list[str] = []
    for key in sorted(mapping):
        value = str(mapping.get(key, "") or "").strip()
        if not value:
            continue
        safe = value.replace("\n", " | ")
        if len(safe) > 180:
            safe = f"{safe[:177]}..."
        parts.append(f"{key}={safe}")
    return " ".join(parts)


def _classify_kasa_exception(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, socket.gaierror):
        return "dns_resolution"
    if isinstance(exc, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(exc, OSError):
        err_no = getattr(exc, "errno", None)
        if err_no in {errno.EHOSTUNREACH, errno.ENETUNREACH}:
            return "host_unreachable"
        if err_no == errno.ECONNREFUSED:
            return "connection_refused"
        if err_no == errno.ETIMEDOUT:
            return "timeout"

    text = str(exc or "").lower()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if (
        "name or service not known" in text
        or "temporary failure in name resolution" in text
        or "nodename nor servname" in text
        or "getaddrinfo failed" in text
        or "dns" in text
    ):
        return "dns_resolution"
    if "connection refused" in text:
        return "connection_refused"
    if "no route to host" in text or "host is unreachable" in text or "network is unreachable" in text:
        return "host_unreachable"
    if "unable to find kasa device" in text:
        return "discovery_failed"
    if "dependency unavailable" in text or "not installed" in text:
        return "dependency_unavailable"
    return "api_or_device_error"


def _run_short_command(args: list[str], *, timeout_s: float = 0.75) -> str:
    executable = str(args[0] if args else "").strip()
    if not executable:
        return "skipped: empty command"
    if shutil.which(executable) is None:
        return "unavailable"
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=max(0.1, float(timeout_s)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"
    output = str(proc.stdout or "").strip()
    if len(output) > _KASA_CONNECTIVITY_MAX_TEXT:
        output = f"{output[:_KASA_CONNECTIVITY_MAX_TEXT - 3]}..."
    if proc.returncode == 0:
        return output or "ok"
    return f"exit={proc.returncode}: {output}" if output else f"exit={proc.returncode}"


def collect_kasa_connectivity_snapshot(device_identifier: str) -> dict[str, str]:
    """Collect a small, bounded local-network snapshot after a Kasa failure."""

    _device_id, host = _split_identifier(device_identifier)
    host = str(host or "").strip()
    snapshot: dict[str, str] = {
        "configured_host": host or "unknown",
        "local_hostname": _run_short_command(["hostname"], timeout_s=0.5),
        "local_ip_addresses": _run_short_command(["hostname", "-I"], timeout_s=0.5),
        "default_route": _run_short_command(
            ["ip", "route", "show", "default"],
            timeout_s=0.5,
        ),
    }

    if host:
        if platform.system().lower().startswith("windows"):
            ping_args = ["ping", "-n", "1", "-w", "1000", host]
        else:
            ping_args = ["ping", "-c", "1", "-W", "1", host]
        snapshot["ping"] = _run_short_command(ping_args, timeout_s=1.3)
        try:
            with socket.create_connection(
                (host, 9999),
                timeout=max(0.1, float(_KASA_CONNECTIVITY_TIMEOUT_S)),
            ):
                snapshot["tcp_9999"] = "open"
        except Exception as exc:
            snapshot["tcp_9999"] = f"{_classify_kasa_exception(exc)}: {exc}"
    else:
        snapshot["ping"] = "skipped: no configured host"
        snapshot["tcp_9999"] = "skipped: no configured host"
    return snapshot


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
        direct_error: Exception | None = None
        if _is_ip_address(host):
            try:
                candidate = await discover_cls.discover_single(host)
            except Exception as exc:
                direct_error = exc
                if not device_id:
                    raise
        if candidate is None:
            try:
                found = await discover_cls.discover()
            except Exception:
                if direct_error is not None:
                    raise direct_error
                raise
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
            if direct_error is not None:
                raise RuntimeError(
                    f"Unable to find Kasa device '{identifier}' after direct-IP "
                    f"lookup failed and discovery fallback found no matching device."
                ) from direct_error
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
        connectivity_probe: Callable[[str], Mapping[str, str]] | None = None,
    ) -> None:
        self._controller = controller
        self._settings_provider = settings_provider
        self._log = log
        self._command_result_callback = command_result_callback
        self._connectivity_probe = connectivity_probe or collect_kasa_connectivity_snapshot
        self._task_q: queue.Queue[_WorkerTask | None] = queue.Queue(maxsize=KASA_TASK_QUEUE_MAXSIZE)
        self._stop_evt = threading.Event()
        self._state_lock = threading.Lock()
        self._router_instance_id = _next_router_instance_id()
        self._router_session_id = _next_router_session_id()
        self._outlet_dispatch_barriers: dict[OutletStateKey, threading.Lock] = {}
        self._alias_to_physical_device: dict[str, str] = {}
        self._dispatch_commitment_by_outlet: dict[
            OutletStateKey, _DispatchCommitment
        ] = {}
        self._last_spindle_state: bool | None = None
        self._last_requested_by_outlet: dict[OutletStateKey, tuple[bool, float]] = {}
        self._accessory_command_seq = 0
        self._latest_accessory_command_by_outlet: dict[OutletStateKey, int] = {}
        self._latest_accessory_identity_by_outlet: dict[
            OutletStateKey, _AccessoryCommandIdentity
        ] = {}
        self._dominant_accessory_command_by_outlet: dict[OutletStateKey, int] = {}
        self._dominant_recovery_identity_by_outlet: dict[
            OutletStateKey, tuple[int, int]
        ] = {}
        self._recovery_off_status_by_outlet: dict[OutletStateKey, str] = {}
        self._cached_settings: dict[str, Any] = {}
        self._cached_device_identifier: str | None = None
        self._cached_device_handle: DeviceHandle | None = None
        self._worker = threading.Thread(target=self._worker_loop, name="kasa-worker", daemon=True)
        self._worker.start()
        try:
            self._cached_settings = dict(self._read_settings())
        except Exception:
            self._cached_settings = {}

    def _retry_delay_s(self, attempt_index: int) -> float:
        delay = _KASA_COMMAND_RETRY_BASE_DELAY_S * (2**max(0, int(attempt_index)))
        return min(_KASA_COMMAND_RETRY_MAX_DELAY_S, max(0.0, float(delay)))

    def _controller_timeout_s(self) -> float | None:
        try:
            timeout = float(getattr(self._controller, "_request_timeout_s"))
        except (TypeError, ValueError, AttributeError):
            return None
        return timeout if timeout > 0 else None

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

    def shutdown(self, timeout: float = 1.0) -> bool:
        with self._state_lock:
            retiring_router_instance_id = int(self._router_instance_id)
            retiring_session_id = int(self._router_session_id)
            self._router_session_id = _next_router_session_id()
        self._stop_evt.set()
        try:
            queue.Queue.put_nowait(self._task_q, None)
        except queue.Full:
            self._log_warning("Kasa shutdown requested while task queue is full; waiting for worker to drain.")
        timeout_s = max(0.0, float(timeout))
        started = time.monotonic()
        dispatch_drained = (
            _PHYSICAL_OUTLET_DISPATCH_REGISTRY.wait_for_router_instance_session(
                router_instance_id=retiring_router_instance_id,
                router_session_id=retiring_session_id,
                timeout=timeout_s,
            )
        )
        remaining = max(0.0, timeout_s - (time.monotonic() - started))
        self._worker.join(timeout=remaining)
        complete = bool(dispatch_drained and not self._worker.is_alive())
        if not complete:
            self._log_warning(
                "Kasa shutdown incomplete; committed physical outlet work may still be in progress."
            )
        return complete

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

    @staticmethod
    def _normalized_alias(identifier: str) -> str:
        return str(identifier or "").strip().casefold()

    @staticmethod
    def _device_id_identity(device_id: str) -> str:
        return f"device-id:{str(device_id or '').strip().casefold()}"

    def _physical_device_identity_for_identifier_locked(
        self,
        identifier: str,
    ) -> str:
        alias = self._normalized_alias(identifier)
        mapped = self._alias_to_physical_device.get(alias)
        if mapped:
            return mapped
        device_id, _host = _split_identifier(identifier)
        if device_id:
            identity = self._device_id_identity(device_id)
            self._alias_to_physical_device[alias] = identity
            return identity
        return _PROVISIONAL_DEVICE_IDENTITY

    def _physical_device_identity_for_handle_locked(
        self,
        configured_identifier: str,
        handle: DeviceHandle,
    ) -> str:
        handle_device_id = str(handle.device_id or "").strip()
        identifier_device_id, _host = _split_identifier(handle.identifier)
        stable_device_id = handle_device_id or str(identifier_device_id or "").strip()
        if stable_device_id:
            identity = self._device_id_identity(stable_device_id)
        else:
            # Controllers without a stable device ID retain a normalized
            # resolved identifier. The provisional safety domain remains in
            # the final authorization set so unresolved aliases fail closed.
            identity = f"resolved:{self._normalized_alias(handle.identifier or handle.ip)}"
        for alias in (configured_identifier, handle.identifier, handle.ip):
            normalized = self._normalized_alias(alias)
            if normalized:
                self._alias_to_physical_device[normalized] = identity
        return identity

    @staticmethod
    def _state_key(device_identity: str, outlet: int) -> OutletStateKey:
        return (str(device_identity), int(outlet))

    def _dispatch_barrier_for(self, state_key: OutletStateKey) -> threading.Lock:
        entry = _PHYSICAL_OUTLET_DISPATCH_REGISTRY.entry_for(state_key)
        _PHYSICAL_OUTLET_DISPATCH_REGISTRY.register_router_instance(
            entry,
            int(self._router_instance_id),
        )
        with self._state_lock:
            barrier = self._outlet_dispatch_barriers.get(state_key)
            if barrier is None:
                barrier = entry.barrier
                self._outlet_dispatch_barriers[state_key] = barrier
            return barrier

    def _dispatch_entry_for(
        self,
        state_key: OutletStateKey,
    ) -> _SharedOutletDispatchEntry:
        entry = _PHYSICAL_OUTLET_DISPATCH_REGISTRY.entry_for(state_key)
        _PHYSICAL_OUTLET_DISPATCH_REGISTRY.register_router_instance(
            entry,
            int(self._router_instance_id),
        )
        with self._state_lock:
            self._outlet_dispatch_barriers.setdefault(state_key, entry.barrier)
        return entry

    def _merge_resolved_command_ownership_locked(
        self,
        *,
        provisional_key: OutletStateKey,
        canonical_key: OutletStateKey,
        identity: _AccessoryCommandIdentity,
    ) -> None:
        provisional_latest = self._latest_accessory_identity_by_outlet.get(
            provisional_key
        )
        candidate = identity
        if (
            provisional_latest is not None
            and int(provisional_latest.accessory_command_id)
            > int(candidate.accessory_command_id)
        ):
            candidate = provisional_latest
        current = self._latest_accessory_identity_by_outlet.get(canonical_key)
        if current is None or int(candidate.accessory_command_id) >= int(
            current.accessory_command_id
        ):
            self._latest_accessory_identity_by_outlet[canonical_key] = candidate
            self._latest_accessory_command_by_outlet[canonical_key] = int(
                candidate.accessory_command_id
            )
        if (
            provisional_latest is not None
            and int(provisional_latest.accessory_command_id)
            == int(identity.accessory_command_id)
        ):
            last_request = self._last_requested_by_outlet.pop(provisional_key, None)
            if last_request is not None:
                self._last_requested_by_outlet[canonical_key] = last_request
        provisional_dominant = self._dominant_accessory_command_by_outlet.get(
            provisional_key
        )
        canonical_dominant = int(
            self._dominant_accessory_command_by_outlet.get(canonical_key, 0)
        )
        if provisional_dominant is not None:
            self._dominant_accessory_command_by_outlet[canonical_key] = max(
                int(provisional_dominant),
                canonical_dominant,
            )
        provisional_recovery = self._dominant_recovery_identity_by_outlet.get(
            provisional_key
        )
        if provisional_recovery is not None:
            current_recovery = self._dominant_recovery_identity_by_outlet.get(
                canonical_key
            )
            if current_recovery is None or provisional_recovery >= current_recovery:
                self._dominant_recovery_identity_by_outlet[canonical_key] = (
                    provisional_recovery
                )
                provisional_status = self._recovery_off_status_by_outlet.get(
                    provisional_key
                )
                if provisional_status and int(provisional_dominant or 0) >= int(
                    canonical_dominant
                ):
                    self._set_recovery_off_status_locked(
                        canonical_key,
                        status=provisional_status,
                        recovery_identity=provisional_recovery,
                    )

    def _set_recovery_off_status_locked(
        self,
        state_key: OutletStateKey,
        *,
        status: str,
        recovery_identity: tuple[int, int],
    ) -> None:
        self._recovery_off_status_by_outlet[state_key] = str(status)
        _PHYSICAL_OUTLET_DISPATCH_REGISTRY.set_recovery_off_status(
            state_key,
            status=str(status),
            recovery_identity=(
                int(recovery_identity[0]),
                int(recovery_identity[1]),
            ),
        )

    def _recovery_off_status_for_locked(self, state_key: OutletStateKey) -> str | None:
        status = self._recovery_off_status_by_outlet.get(state_key)
        if status:
            return str(status)
        return _PHYSICAL_OUTLET_DISPATCH_REGISTRY.recovery_off_status(state_key)

    @staticmethod
    def _work_scope_is_newer(
        candidate: _AccessoryCommandIdentity,
        current: _AccessoryCommandIdentity,
    ) -> bool:
        return (
            int(candidate.connection_generation),
            int(candidate.recovery_epoch),
            int(candidate.stream_epoch),
            int(candidate.source_id),
        ) > (
            int(current.connection_generation),
            int(current.recovery_epoch),
            int(current.stream_epoch),
            int(current.source_id),
        )

    def _accessory_dispatch_authorized_locked(
        self,
        state_key: OutletStateKey,
        identity: _AccessoryCommandIdentity,
        *,
        safety_keys: tuple[OutletStateKey, ...] = (),
    ) -> bool:
        if (
            self._stop_evt.is_set()
            or int(identity.router_instance_id) != int(self._router_instance_id)
            or int(identity.router_session_id) != int(self._router_session_id)
        ):
            return False
        keys = tuple(dict.fromkeys((state_key, *safety_keys)))
        for key in keys:
            dominant_command_id = self._dominant_accessory_command_by_outlet.get(
                key, 0
            )
            if int(dominant_command_id) > int(identity.accessory_command_id):
                return False
            dominant_recovery = self._dominant_recovery_identity_by_outlet.get(key)
            if identity.desired_on and (
                dominant_recovery is not None
                or self._recovery_off_status_for_locked(key) is not None
            ):
                return False
        dominant_command_id = self._dominant_accessory_command_by_outlet.get(
            state_key, 0
        )
        dominant_recovery = self._dominant_recovery_identity_by_outlet.get(state_key)
        if identity.safety_priority:
            if identity.desired_on:
                return False
            if int(dominant_command_id) != int(identity.accessory_command_id):
                return False
            if dominant_recovery != (
                int(identity.connection_generation),
                int(identity.recovery_epoch),
            ):
                return False
        latest_identity = self._latest_accessory_identity_by_outlet.get(state_key)
        if latest_identity is not None and self._work_scope_is_newer(
            latest_identity, identity
        ):
            return False
        return True

    def _accessory_result_is_current_locked(
        self,
        state_key: OutletStateKey,
        identity: _AccessoryCommandIdentity,
        *,
        alias_keys: tuple[OutletStateKey, ...] = (),
    ) -> bool:
        if not self._accessory_dispatch_authorized_locked(
            state_key,
            identity,
            safety_keys=alias_keys,
        ):
            return False
        for key in tuple(dict.fromkeys((state_key, *alias_keys))):
            latest = self._latest_accessory_identity_by_outlet.get(key)
            if (
                latest is not None
                and int(latest.accessory_command_id)
                > int(identity.accessory_command_id)
            ):
                return False
        return bool(
            self._latest_accessory_command_by_outlet.get(state_key)
            == int(identity.accessory_command_id)
        )

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
        try:
            task = _WorkerTask(
                func=func,
                on_success=on_success,
                on_error=on_error,
                description=description,
            )
            self._task_q.put_nowait(task)
        except queue.Full:
            err = RuntimeError(f"Kasa task queue full; dropping task '{description}'.")
            if on_error is not None:
                self._invoke_callback(on_error, err)
            else:
                self._log_warning(str(err))
            return False
        except Exception as exc:
            err = RuntimeError(f"Kasa task submission failed for '{description}': {exc}")
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
        try:
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
                            self._log_warning(
                                f"Kasa task '{task.description}' failed: {exc}"
                            )
                        continue
                    if task.on_success is not None:
                        self._invoke_callback(task.on_success, result)
                finally:
                    self._task_q.task_done()
        finally:
            _PHYSICAL_OUTLET_DISPATCH_REGISTRY.unregister_router_instance(
                int(self._router_instance_id)
            )

    def _clear_device_cache(self) -> None:
        with self._state_lock:
            self._cached_device_identifier = None
            self._cached_device_handle = None

    def _device_handle_for_identifier(self, device_identifier: str) -> DeviceHandle:
        handle, _cache_status = self._device_handle_for_identifier_with_cache_status(
            device_identifier
        )
        return handle

    def _device_handle_for_identifier_with_cache_status(
        self, device_identifier: str
    ) -> tuple[DeviceHandle, str]:
        with self._state_lock:
            if (
                self._cached_device_identifier == device_identifier
                and self._cached_device_handle is not None
            ):
                return self._cached_device_handle, "cached"
        handle = self._controller.connect(device_identifier)
        with self._state_lock:
            self._cached_device_identifier = device_identifier
            self._cached_device_handle = handle
        return handle, "reconnected"

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
        line_index: int | None = None,
        connection_generation: int | None = None,
        stream_epoch: int | None = None,
        recovery_epoch: int | None = None,
        source_id: int | None = None,
        safety_priority: bool = False,
        _settings_override: Mapping[str, Any] | None = None,
    ) -> bool:
        settings = (
            dict(_settings_override)
            if _settings_override is not None
            else self._read_settings()
        )
        with self._state_lock:
            self._cached_settings = dict(settings)
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
        request_generation = int(connection_generation or 0)
        request_stream_epoch = int(stream_epoch or 0)
        request_recovery_epoch = int(recovery_epoch or 0)
        request_source_id = int(source_id or 0)
        request_recovery_identity = (
            request_generation,
            request_recovery_epoch,
        )
        with self._state_lock:
            registration_device_identity = (
                self._physical_device_identity_for_identifier_locked(
                    device_identifier
                )
            )
        registration_state_key = self._state_key(
            registration_device_identity,
            outlet,
        )
        provisional_state_key = self._state_key(
            _PROVISIONAL_DEVICE_IDENTITY,
            outlet,
        )
        registration_barrier = self._dispatch_barrier_for(registration_state_key)
        resolved_state_key = [registration_state_key]
        request_ts = time.monotonic()
        timeout_s = self._controller_timeout_s()
        max_attempts = max(1, int(_KASA_COMMAND_RETRY_MAX_ATTEMPTS))
        command_text = "ON" if desired else "OFF"
        _configured_device_id, configured_host = _split_identifier(device_identifier)
        line_text = f" line_index={int(line_index)}" if line_index is not None else ""
        timeout_text = f" timeout={timeout_s:.1f}s" if timeout_s is not None else ""
        source_key = str(source or "").strip().lower()
        dominant_off = bool(
            (not desired)
            and (
                bool(safety_priority)
                or source_key
                in {
                    "job_recovery_required",
                    "job_all_stop",
                    "job_reset",
                    "app_exit",
                }
            )
        )
        # A non-blocking acquire gives recovery a precise order without making
        # CNC recovery wait for an already-dispatched network operation. If the
        # barrier is busy, that operation crossed the physical-dispatch boundary
        # first; the tombstone still commits immediately and suppresses all work
        # that has not crossed that boundary.
        registration_barrier_owned = bool(
            dominant_off and registration_barrier.acquire(blocking=False)
        )
        try:
            with self._state_lock:
                dominant_identity = self._dominant_recovery_identity_by_outlet.get(
                    registration_state_key
                )
                if desired and (
                    dominant_identity is not None
                    or self._recovery_off_status_for_locked(registration_state_key)
                    is not None
                ):
                    return False
                last_request = self._last_requested_by_outlet.get(
                    registration_state_key
                )
                if last_request is not None:
                    last_desired, last_ts = last_request
                    if (
                        not bool(safety_priority)
                        and last_desired is desired
                        and (request_ts - last_ts) < _KASA_DUPLICATE_REQUEST_WINDOW_S
                    ):
                        return False
                self._accessory_command_seq += 1
                accessory_command_id = int(self._accessory_command_seq)
                command_identity = _AccessoryCommandIdentity(
                    router_instance_id=int(self._router_instance_id),
                    router_session_id=int(self._router_session_id),
                    connection_generation=request_generation,
                    stream_epoch=request_stream_epoch,
                    recovery_epoch=request_recovery_epoch,
                    source_id=request_source_id,
                    accessory_command_id=accessory_command_id,
                    source=str(source or ""),
                    desired_on=desired,
                    safety_priority=bool(safety_priority),
                )
                self._latest_accessory_command_by_outlet[registration_state_key] = (
                    accessory_command_id
                )
                self._latest_accessory_identity_by_outlet[
                    registration_state_key
                ] = command_identity
                if dominant_off:
                    self._dominant_accessory_command_by_outlet[
                        registration_state_key
                    ] = (
                        accessory_command_id
                    )
                    if bool(safety_priority):
                        self._dominant_recovery_identity_by_outlet[
                            registration_state_key
                        ] = request_recovery_identity
                        self._set_recovery_off_status_locked(
                            registration_state_key,
                            status="requested",
                            recovery_identity=request_recovery_identity,
                        )
        finally:
            if registration_barrier_owned:
                registration_barrier.release()

        logger.info(
            "Kasa outlet command requested: outlet=%d command=%s source=%s device=%s host=%s%s attempts=%d%s",
            int(outlet),
            command_text,
            str(source),
            device_identifier,
            str(configured_host or "unknown"),
            line_text,
            int(max_attempts),
            timeout_text,
        )

        task_started_s = time.monotonic()
        task_attempts = 0
        task_device_ip: str | None = None
        task_failure_kind: str | None = None

        def _task() -> dict[str, Any]:
            nonlocal task_attempts, task_device_ip, task_failure_kind
            last_error: Exception | None = None
            for attempt in range(max_attempts):
                with self._state_lock:
                    if not self._accessory_dispatch_authorized_locked(
                        registration_state_key,
                        command_identity,
                        safety_keys=(provisional_state_key,),
                    ):
                        return {
                            "attempts": int(task_attempts),
                            "device_ip": task_device_ip,
                            "elapsed_s": float(time.monotonic() - task_started_s),
                            "superseded": True,
                        }
                task_attempts = int(attempt + 1)
                attempt_started_s = time.monotonic()
                try:
                    handle, cache_status = self._device_handle_for_identifier_with_cache_status(
                        device_identifier
                    )
                    with self._state_lock:
                        physical_identity = (
                            self._physical_device_identity_for_handle_locked(
                                device_identifier,
                                handle,
                            )
                        )
                        canonical_state_key = self._state_key(
                            physical_identity,
                            outlet,
                        )
                        self._merge_resolved_command_ownership_locked(
                            provisional_key=registration_state_key,
                            canonical_key=canonical_state_key,
                            identity=command_identity,
                        )
                        resolved_state_key[0] = canonical_state_key
                    dispatch_entry = self._dispatch_entry_for(canonical_state_key)
                    dispatch_barrier = dispatch_entry.barrier
                    task_device_ip = str(handle.ip or "") or None
                    logger.info(
                        "Kasa outlet command attempt: outlet=%d command=%s source=%s device=%s host=%s ip=%s device_cache=%s attempt=%d/%d%s%s",
                        int(outlet),
                        command_text,
                        str(source),
                        device_identifier,
                        str(configured_host or "unknown"),
                        str(task_device_ip or "unknown"),
                        str(cache_status),
                        int(attempt + 1),
                        int(max_attempts),
                        line_text,
                        timeout_text,
                    )
                    with dispatch_barrier:
                        with self._state_lock:
                            dispatch_authorized = (
                                self._accessory_dispatch_authorized_locked(
                                    canonical_state_key,
                                    command_identity,
                                    safety_keys=(
                                        registration_state_key,
                                        provisional_state_key,
                                    ),
                                )
                            )
                            if dispatch_authorized:
                                # This state-lock commit, while the canonical
                                # physical-outlet barrier is held, is the exact
                                # software dispatch linearization point.
                                dispatch_commitment = _DispatchCommitment(
                                    identity=command_identity,
                                    device_identifier=device_identifier,
                                )
                                self._dispatch_commitment_by_outlet[
                                    canonical_state_key
                                ] = dispatch_commitment
                        if not dispatch_authorized:
                            return {
                                "attempts": int(task_attempts),
                                "device_ip": task_device_ip,
                                "elapsed_s": float(
                                    time.monotonic() - task_started_s
                                ),
                                "superseded": True,
                            }
                        # The network API call follows the committed boundary.
                        # The per-outlet barrier remains held, but the global
                        # state lock does not, so unrelated outlets and state
                        # reads are not serialized behind device/network I/O.
                        _PHYSICAL_OUTLET_DISPATCH_REGISTRY.begin_committed(
                            dispatch_entry,
                            dispatch_commitment,
                        )
                        try:
                            self._controller.set_outlet_state(handle, outlet, desired)
                        finally:
                            _PHYSICAL_OUTLET_DISPATCH_REGISTRY.finish_committed(
                                dispatch_entry,
                                dispatch_commitment,
                            )
                            with self._state_lock:
                                commitment = self._dispatch_commitment_by_outlet.get(
                                    canonical_state_key
                                )
                                if (
                                    commitment is not None
                                    and commitment.identity is command_identity
                                ):
                                    self._dispatch_commitment_by_outlet.pop(
                                        canonical_state_key,
                                        None,
                                    )
                    elapsed_s = time.monotonic() - task_started_s
                    attempt_elapsed_s = time.monotonic() - attempt_started_s
                    logger.info(
                        "Kasa outlet command succeeded: outlet=%d command=%s source=%s device=%s ip=%s attempt=%d/%d elapsed=%.3fs attempt_elapsed=%.3fs final_state=unconfirmed%s",
                        int(outlet),
                        command_text,
                        str(source),
                        device_identifier,
                        str(task_device_ip or "unknown"),
                        int(attempt + 1),
                        int(max_attempts),
                        float(elapsed_s),
                        float(attempt_elapsed_s),
                        line_text,
                    )
                    return {
                        "attempts": int(attempt + 1),
                        "device_ip": task_device_ip,
                        "elapsed_s": float(elapsed_s),
                    }
                except Exception as exc:
                    last_error = exc
                    task_failure_kind = _classify_kasa_exception(exc)
                    self._clear_device_cache()
                    if attempt >= (max_attempts - 1):
                        break
                    retry_delay_s = self._retry_delay_s(attempt)
                    attempt_elapsed_s = time.monotonic() - attempt_started_s
                    logger.debug(
                        "Kasa outlet command retry scheduled: outlet=%d command=%s source=%s attempt=%d/%d delay=%.2fs attempt_elapsed=%.3fs failure_kind=%s exception=%s err=%s%s",
                        int(outlet),
                        command_text,
                        str(source),
                        int(attempt + 1),
                        int(max_attempts),
                        float(retry_delay_s),
                        float(attempt_elapsed_s),
                        str(task_failure_kind),
                        type(exc).__name__,
                        str(exc),
                        line_text,
                    )
                    time.sleep(retry_delay_s)
            with self._state_lock:
                state_key = resolved_state_key[0]
                if (
                    self._latest_accessory_command_by_outlet.get(state_key)
                    == accessory_command_id
                ):
                    self._last_requested_by_outlet.pop(state_key, None)
            if last_error is None:
                raise RuntimeError("Kasa outlet command failed with unknown error.")
            raise last_error

        def _on_success(info: Any) -> None:
            info_map = info if isinstance(info, dict) else {}
            with self._state_lock:
                state_key = resolved_state_key[0]
                current_command = bool(
                    not bool(info_map.get("superseded", False))
                    and self._accessory_result_is_current_locked(
                        state_key,
                        command_identity,
                        alias_keys=(registration_state_key, provisional_state_key),
                    )
                )
                if current_command:
                    self._last_requested_by_outlet[state_key] = (desired, time.monotonic())
                current_recovery_command = bool(
                    safety_priority
                    and current_command
                    and self._dominant_accessory_command_by_outlet.get(state_key)
                    == accessory_command_id
                    and self._dominant_recovery_identity_by_outlet.get(state_key)
                    == request_recovery_identity
                )
                if current_recovery_command:
                    for recovery_key in tuple(
                        dict.fromkeys(
                            (
                                state_key,
                                registration_state_key,
                                provisional_state_key,
                            )
                        )
                    ):
                        if (
                            self._dominant_recovery_identity_by_outlet.get(
                                recovery_key
                            )
                            == request_recovery_identity
                        ):
                            self._set_recovery_off_status_locked(
                                recovery_key,
                                status="confirmed",
                                recovery_identity=request_recovery_identity,
                            )
            superseded = bool(info_map.get("superseded", False) or not current_command)
            self._emit_command_result(
                OutletCommandResult(
                    outlet_id=outlet,
                    on=desired,
                    success=True,
                    source=source,
                    error=None,
                    device_identifier=device_identifier,
                    device_ip=str(info_map.get("device_ip") or task_device_ip or "") or None,
                    line_index=line_index,
                    attempts=int(info_map.get("attempts") or task_attempts or 1),
                    max_attempts=max_attempts,
                    timeout_s=timeout_s,
                    elapsed_s=float(info_map.get("elapsed_s") or (time.monotonic() - task_started_s)),
                    final_state=None,
                    failure_kind=None,
                    connectivity=None,
                    connection_generation=request_generation,
                    stream_epoch=request_stream_epoch,
                    recovery_epoch=request_recovery_epoch,
                    source_id=request_source_id,
                    accessory_command_id=accessory_command_id,
                    safety_priority=bool(safety_priority),
                    superseded=superseded,
                )
            )

        def _on_error(exc: Exception) -> None:
            elapsed_s = time.monotonic() - task_started_s
            failure_kind = task_failure_kind or _classify_kasa_exception(exc)
            try:
                connectivity = dict(self._connectivity_probe(device_identifier) or {})
            except Exception as probe_exc:
                connectivity = {
                    "probe_error": f"{type(probe_exc).__name__}: {probe_exc}"
                }
            connectivity_text = _format_mapping(connectivity)
            with self._state_lock:
                state_key = resolved_state_key[0]
                current_command = bool(
                    self._accessory_result_is_current_locked(
                        state_key,
                        command_identity,
                        alias_keys=(registration_state_key, provisional_state_key),
                    )
                )
                if current_command:
                    self._last_requested_by_outlet.pop(state_key, None)
                current_recovery_command = bool(
                    safety_priority
                    and current_command
                    and self._dominant_accessory_command_by_outlet.get(state_key)
                    == accessory_command_id
                    and self._dominant_recovery_identity_by_outlet.get(state_key)
                    == request_recovery_identity
                )
                if current_recovery_command:
                    for recovery_key in tuple(
                        dict.fromkeys(
                            (
                                state_key,
                                registration_state_key,
                                provisional_state_key,
                            )
                        )
                    ):
                        if (
                            self._dominant_recovery_identity_by_outlet.get(
                                recovery_key
                            )
                            == request_recovery_identity
                        ):
                            self._set_recovery_off_status_locked(
                                recovery_key,
                                status="failed_unknown",
                                recovery_identity=request_recovery_identity,
                            )
            self._log_warning(
                f"Kasa outlet {outlet} command failed ({source}): {exc} "
                f"(command={command_text}, attempts={task_attempts or 0}/{max_attempts}, "
                f"elapsed={elapsed_s:.3f}s, failure_kind={failure_kind}, "
                f"exception={type(exc).__name__}, device={device_identifier}, "
                f"host={configured_host or 'unknown'}, ip={task_device_ip or 'unknown'}"
                f"{line_text}, connectivity={connectivity_text or 'unavailable'})"
            )
            self._emit_command_result(
                OutletCommandResult(
                    outlet_id=outlet,
                    on=desired,
                    success=False,
                    source=source,
                    error=str(exc),
                    device_identifier=device_identifier,
                    device_ip=task_device_ip,
                    line_index=line_index,
                    attempts=task_attempts or 0,
                    max_attempts=max_attempts,
                    timeout_s=timeout_s,
                    elapsed_s=float(elapsed_s),
                    final_state=None,
                    failure_kind=failure_kind,
                    connectivity=connectivity,
                    connection_generation=request_generation,
                    stream_epoch=request_stream_epoch,
                    recovery_epoch=request_recovery_epoch,
                    source_id=request_source_id,
                    accessory_command_id=accessory_command_id,
                    safety_priority=bool(safety_priority),
                    superseded=not current_command,
                )
            )

        try:
            accepted = self._submit_task(
                _task,
                description=f"set_outlet_state:{outlet}:{desired}",
                on_success=_on_success,
                on_error=_on_error,
            )
        except Exception as exc:  # defensive boundary for injected/custom submitters
            accepted = False
            self._log_warning(
                f"Kasa outlet {outlet} task submission raised ({source}): {exc}"
            )
        if not accepted:
            with self._state_lock:
                state_key = registration_state_key
                current_command = bool(
                    self._latest_accessory_command_by_outlet.get(state_key)
                    == accessory_command_id
                )
                current_recovery_command = bool(
                    safety_priority
                    and current_command
                    and self._dominant_accessory_command_by_outlet.get(state_key)
                    == accessory_command_id
                    and self._dominant_recovery_identity_by_outlet.get(state_key)
                    == request_recovery_identity
                )
                if current_recovery_command:
                    # This command identity remains as a persistent safety
                    # tombstone even though no physical OFF task was admitted.
                    # Older queued ON work must remain suppressed.
                    self._set_recovery_off_status_locked(
                        state_key,
                        status="failed_unknown",
                        recovery_identity=request_recovery_identity,
                    )
                    self._last_requested_by_outlet.pop(state_key, None)
                elif current_command:
                    self._latest_accessory_command_by_outlet.pop(state_key, None)
                    self._latest_accessory_identity_by_outlet.pop(state_key, None)
                    if (
                        self._dominant_accessory_command_by_outlet.get(state_key)
                        == accessory_command_id
                    ):
                        self._dominant_accessory_command_by_outlet.pop(state_key, None)
            return False
        with self._state_lock:
            if (
                self._latest_accessory_command_by_outlet.get(registration_state_key)
                == accessory_command_id
            ):
                self._last_requested_by_outlet[registration_state_key] = (
                    desired,
                    request_ts,
                )
        return True

    def request_recovery_safety_off(
        self,
        *,
        connection_generation: int,
        stream_epoch: int,
        recovery_epoch: int,
        source_id: int,
    ) -> int:
        """Immediately submit dominant OFF work without depending on UI delivery."""
        with self._state_lock:
            settings = dict(self._cached_settings)
            identifier = str(settings.get("kasa_device_identifier", "") or "").strip()
            configured_identity = (
                self._physical_device_identity_for_identifier_locked(identifier)
                if identifier
                else ""
            )
            known_on = {
                outlet
                for (device, outlet), (desired, _ts) in self._last_requested_by_outlet.items()
                if device in {configured_identity, _PROVISIONAL_DEVICE_IDENTITY}
                and desired
            }
        if not settings.get("kasa_enabled") or not identifier:
            return 0
        outlets = set(known_on)
        if bool(settings.get("vacuum_enabled", False)):
            outlets.add(_coerce_outlet_id(settings.get("vacuum_outlet", 1), default=1))
        if bool(settings.get("light_enabled", False)):
            outlets.add(_coerce_outlet_id(settings.get("light_outlet", 2), default=2))
        submitted = 0
        for outlet in sorted(outlets):
            if self.request_outlet_state(
                int(outlet),
                False,
                source="job_recovery_required",
                connection_generation=int(connection_generation),
                stream_epoch=int(stream_epoch),
                recovery_epoch=int(recovery_epoch),
                source_id=int(source_id),
                safety_priority=True,
                _settings_override=settings,
            ):
                submitted += 1
        return submitted

    def recovery_off_status(self) -> dict[OutletStateKey, str]:
        with self._state_lock:
            statuses = _PHYSICAL_OUTLET_DISPATCH_REGISTRY.recovery_off_status_snapshot()
            statuses.update(self._recovery_off_status_by_outlet)
            return dict(statuses)

    def retire_confirmed_recovery_safety_off(
        self,
        *,
        connection_generation: int,
        recovery_epoch: int,
    ) -> int:
        """Retire confirmed recovery-only OFF dominance after recovery succeeds."""
        recovery_identity = (int(connection_generation), int(recovery_epoch))
        retired = 0
        with self._state_lock:
            statuses = _PHYSICAL_OUTLET_DISPATCH_REGISTRY.recovery_off_status_snapshot()
            statuses.update(self._recovery_off_status_by_outlet)
            for state_key, status in list(statuses.items()):
                if str(status) != "confirmed":
                    continue
                local_identity = self._dominant_recovery_identity_by_outlet.get(state_key)
                registry_identity = _PHYSICAL_OUTLET_DISPATCH_REGISTRY.recovery_off_identity(
                    state_key
                )
                if local_identity not in (None, recovery_identity):
                    continue
                if registry_identity != recovery_identity:
                    continue
                if (
                    _PHYSICAL_OUTLET_DISPATCH_REGISTRY.active_count(state_key)
                    > 0
                ):
                    continue
                if not _PHYSICAL_OUTLET_DISPATCH_REGISTRY.retire_recovery_off_status(
                    state_key,
                    recovery_identity=recovery_identity,
                ):
                    continue
                self._dominant_recovery_identity_by_outlet.pop(state_key, None)
                self._recovery_off_status_by_outlet.pop(state_key, None)
                retired += 1
        return retired

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
