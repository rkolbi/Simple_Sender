import asyncio
import queue

import pytest

from simple_sender.kasa_accessory import (
    AccessoryRouter,
    FakeKasaController,
    PythonKasaController,
    validate_outlet_mapping,
)


pytestmark = pytest.mark.unit


def _make_settings() -> dict[str, object]:
    return {
        "kasa_enabled": True,
        "kasa_device_identifier": "FAKE-DEVICE@192.168.0.50",
        "vacuum_enabled": True,
        "vacuum_outlet": 1,
        "light_enabled": True,
        "light_outlet": 2,
    }


def _build_router(
    settings: dict[str, object],
    *,
    raise_on_set: bool = False,
    outlet_count: int = 2,
):
    logs: list[str] = []
    results = []
    controller = FakeKasaController(
        device_identifier=str(settings["kasa_device_identifier"]),
        raise_on_set=raise_on_set,
        outlet_count=outlet_count,
    )
    router = AccessoryRouter(
        controller=controller,
        settings_provider=lambda: settings,
        log=logs.append,
        command_result_callback=results.append,
    )
    return router, controller, logs, results


def test_spindle_on_triggers_enabled_outlets() -> None:
    settings = _make_settings()
    router, controller, _, _ = _build_router(settings)
    try:
        router.on_spindle_state_change(True)
        assert router.wait_for_idle()
        assert controller.commands == [
            (str(settings["kasa_device_identifier"]), 1, True),
            (str(settings["kasa_device_identifier"]), 2, True),
        ]
    finally:
        router.shutdown()


def test_spindle_off_triggers_enabled_outlets() -> None:
    settings = _make_settings()
    router, controller, _, _ = _build_router(settings)
    try:
        router.on_spindle_state_change(True)
        router.on_spindle_state_change(False)
        assert router.wait_for_idle()
        assert controller.commands == [
            (str(settings["kasa_device_identifier"]), 1, True),
            (str(settings["kasa_device_identifier"]), 2, True),
            (str(settings["kasa_device_identifier"]), 1, False),
            (str(settings["kasa_device_identifier"]), 2, False),
        ]
    finally:
        router.shutdown()


def test_debounce_prevents_repeated_spindle_commands() -> None:
    settings = _make_settings()
    router, controller, _, _ = _build_router(settings)
    try:
        router.on_spindle_state_change(True)
        router.on_spindle_state_change(True)
        router.on_spindle_state_change(False)
        router.on_spindle_state_change(False)
        assert router.wait_for_idle()
        assert controller.commands == [
            (str(settings["kasa_device_identifier"]), 1, True),
            (str(settings["kasa_device_identifier"]), 2, True),
            (str(settings["kasa_device_identifier"]), 1, False),
            (str(settings["kasa_device_identifier"]), 2, False),
        ]
    finally:
        router.shutdown()


def test_master_disable_blocks_all_commands() -> None:
    settings = _make_settings()
    settings["kasa_enabled"] = False
    router, controller, _, _ = _build_router(settings)
    try:
        router.on_spindle_state_change(True)
        assert router.wait_for_idle()
        assert controller.commands == []
    finally:
        router.shutdown()


def test_per_function_disable_only_toggles_enabled_outlet() -> None:
    settings = _make_settings()
    settings["vacuum_enabled"] = False
    router, controller, _, _ = _build_router(settings)
    try:
        router.on_spindle_state_change(True)
        router.on_spindle_state_change(False)
        assert router.wait_for_idle()
        assert controller.commands == [
            (str(settings["kasa_device_identifier"]), 2, True),
            (str(settings["kasa_device_identifier"]), 2, False),
        ]
    finally:
        router.shutdown()


def test_outlet_collision_mapping_is_rejected() -> None:
    settings = _make_settings()
    settings["vacuum_outlet"] = 1
    settings["light_outlet"] = 1
    valid, _ = validate_outlet_mapping(
        vacuum_enabled=True,
        vacuum_outlet=1,
        light_enabled=True,
        light_outlet=1,
    )
    assert valid is False

    router, controller, logs, _ = _build_router(settings)
    try:
        router.on_spindle_state_change(True)
        assert router.wait_for_idle()
        assert controller.commands == []
        assert any("mapping invalid" in entry.lower() for entry in logs)
    finally:
        router.shutdown()


def test_set_outlet_errors_are_caught_and_logged() -> None:
    settings = _make_settings()
    router, controller, logs, results = _build_router(settings, raise_on_set=True)
    try:
        router.on_spindle_state_change(True)
        assert router.wait_for_idle()
        assert controller.commands == []
        assert any("failed" in entry.lower() for entry in logs)
        assert results
        assert all(result.success is False for result in results)
    finally:
        router.shutdown()


def test_single_outlet_device_uses_vacuum_only() -> None:
    settings = _make_settings()
    settings["kasa_outlet_count"] = 1
    router, controller, _, _ = _build_router(settings, outlet_count=1)
    try:
        router.on_spindle_state_change(True)
        router.on_spindle_state_change(False)
        assert router.wait_for_idle()
        assert controller.commands == [
            (str(settings["kasa_device_identifier"]), 1, True),
            (str(settings["kasa_device_identifier"]), 1, False),
        ]
    finally:
        router.shutdown()


def test_python_kasa_controller_run_coro_returns_result() -> None:
    controller = PythonKasaController(request_timeout_s=0.2)

    async def _quick() -> str:
        await asyncio.sleep(0.01)
        return "ok"

    assert controller._run_coro(_quick()) == "ok"


def test_python_kasa_controller_run_coro_times_out() -> None:
    controller = PythonKasaController(request_timeout_s=0.01)

    async def _slow() -> str:
        await asyncio.sleep(0.2)
        return "late"

    with pytest.raises(TimeoutError, match="Kasa operation timed out"):
        controller._run_coro(_slow())


def test_submit_task_logs_when_queue_is_full() -> None:
    settings = _make_settings()
    router, _controller, logs, _results = _build_router(settings)

    try:
        router._task_q.put_nowait = lambda _task: (_ for _ in ()).throw(queue.Full())
        router.discover()
        assert any("queue full" in entry.lower() for entry in logs)
    finally:
        router.shutdown()
