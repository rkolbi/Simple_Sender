import os
import time

import pytest

from simple_sender.kasa_accessory import create_default_kasa_controller


pytestmark = [pytest.mark.integration]


def test_kasa_device_toggle_roundtrip() -> None:
    identifier = os.getenv("KASA_TEST_DEVICE_IP", "").strip()
    outlet_1 = os.getenv("KASA_TEST_OUTLET_INDEX_1", "").strip()
    outlet_2 = os.getenv("KASA_TEST_OUTLET_INDEX_2", "").strip()

    if not identifier or not outlet_1:
        pytest.skip("Set KASA_TEST_DEVICE_IP and KASA_TEST_OUTLET_INDEX_1 to run this test.")

    try:
        outlet_1_index = int(outlet_1)
    except ValueError:
        pytest.skip("KASA_TEST_OUTLET_INDEX_1 must be an integer.")

    controller = create_default_kasa_controller()
    try:
        handle = controller.connect(identifier)
    except RuntimeError as exc:
        pytest.skip(str(exc))

    outlets = controller.list_outlets(handle)
    assert outlets, "No outlets reported by selected Kasa device."

    controller.set_outlet_state(handle, outlet_1_index, True)
    time.sleep(0.2)
    controller.set_outlet_state(handle, outlet_1_index, False)

    if outlet_2:
        try:
            outlet_2_index = int(outlet_2)
        except ValueError:
            pytest.skip("KASA_TEST_OUTLET_INDEX_2 must be an integer when provided.")
        controller.set_outlet_state(handle, outlet_2_index, True)
        time.sleep(0.2)
        controller.set_outlet_state(handle, outlet_2_index, False)
