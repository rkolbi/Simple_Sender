#!/usr/bin/env python3
"""Code-defined built-in workflow implementations."""

from __future__ import annotations

from typing import Any

from simple_sender import tool_measurement

_SAFE_MACHINE_Z_MM = -5.0
_TOUCHPLATE_CLEARANCE_RETRACT_MM = 14.0
_TOUCHPLATE_RETRACT_MM = 2.0
_TOUCHPLATE_FINE_PROBE_DISTANCE_MM = 3.0
_TOUCHPLATE_CLEAR_Z_MM = 16.0
_TOUCHPLATE_FINAL_Z_MM = 20.0
_TOOL_SENSOR_RETRACT_MM = 5.0
_XYZ_RETRACT_X_MM = -14.0
_XYZ_RETRACT_Y_MM = -14.0
_XYZ_CLEAR_X_MM = -15.0
_XYZ_CLEAR_Y_MM = -15.0
_XYZ_SECOND_SIDE_X_MM = 30.0


def _state_namespace(executor) -> Any:
    with executor.macro_vars() as macro_vars:
        macro_ns = macro_vars["macro"]
        state_ns = getattr(macro_ns, "state", None)
        if state_ns is None:
            raise RuntimeError("Workflow state namespace is unavailable.")
        return state_ns


def _get_var(executor, key: str, default: Any = None) -> Any:
    with executor.macro_vars() as macro_vars:
        return macro_vars.get(str(key), default)


def _set_var(executor, key: str, value: Any) -> None:
    with executor.macro_vars() as macro_vars:
        macro_vars[str(key)] = value


def _set_state(executor, key: str, value: Any) -> None:
    state_ns = _state_namespace(executor)
    setattr(state_ns, str(key), value)


def _get_state(executor, key: str, default: Any = None) -> Any:
    state_ns = _state_namespace(executor)
    return getattr(state_ns, str(key), default)


def _safe_wait_for_idle(executor) -> None:
    line_timeout_s = float(executor._macro_line_timeout_s())
    executor._macro_wait_for_idle(timeout_s=(line_timeout_s if line_timeout_s > 0 else 30.0))


def _wait_for_fresh_status(executor) -> None:
    line_timeout_s = float(executor._macro_line_timeout_s())
    timeout_s = min(5.0, line_timeout_s) if line_timeout_s > 0 else 1.0
    executor._macro_wait_for_status(timeout_s=max(timeout_s, 0.1))


def _read_float_setting(executor, key: str, default: float, *, min_value: float | None = None) -> float:
    settings = getattr(executor.app, "settings", None)
    raw = default
    if isinstance(settings, dict):
        raw = settings.get(str(key), default)
    try:
        value = float(raw)
    except Exception:
        value = float(default)
    if min_value is not None and value < float(min_value):
        return float(default)
    return float(value)


def _grbl_z_max_travel(executor, workflow_label: str) -> float:
    settings_data = getattr(getattr(executor.app, "settings_controller", None), "_settings_data", {}) or {}
    entry = settings_data.get("$132")
    raw = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
    text = str(raw or "").strip()
    if not text:
        raise RuntimeError(f"GRBL $132 (max Z travel) is required before running {workflow_label}; refresh settings first.")
    try:
        value = float(text)
    except Exception as exc:
        raise RuntimeError(f"GRBL $132 (max Z travel) is required before running {workflow_label}; refresh settings first.") from exc
    if value <= 0.0:
        raise RuntimeError(f"GRBL $132 (max Z travel) must be greater than zero before running {workflow_label}.")
    return float(value)


def _log_work_position_snapshot(executor, prefix: str, *, include_tool_reference: bool = False) -> None:
    with executor.macro_vars() as macro_vars:
        base = (
            f"{prefix}: units={macro_vars.get('units')} WCS={macro_vars.get('WCS')} "
            f"wx={macro_vars.get('wx')} wy={macro_vars.get('wy')} wz={macro_vars.get('wz')} "
            f"mx={macro_vars.get('mx')} my={macro_vars.get('my')} mz={macro_vars.get('mz')}"
        )
        if include_tool_reference:
            base += f" TOOL_REFERENCE={getattr(macro_vars['macro'].state, 'TOOL_REFERENCE', None)}"
    executor.ui_q.put(("log", f"[workflow] {base}"))


def _fixed_sensor_probe_settings(executor) -> tuple[tool_measurement.ToolProbeCycleSettings, float, float, float, float]:
    cycle = tool_measurement.tool_probe_cycle_settings(executor.app)
    probe_z_location = _read_float_setting(executor, "macro_probe_z_location", -5.0)
    probe_safety_margin = max(0.0, _read_float_setting(executor, "macro_probe_safety_margin", 3.0))
    spread_tolerance_mm = max(
        0.001,
        _read_float_setting(
            executor,
            "macro_tool_probe_spread_tolerance_mm",
            tool_measurement.DEFAULT_TOOL_PROBE_SPREAD_TOLERANCE_MM,
        ),
    )
    z_max_travel = _grbl_z_max_travel(executor, "Job Setup")
    probe_distance = max(1.0, round(z_max_travel + probe_z_location - probe_safety_margin, 3))
    return cycle, probe_z_location, probe_safety_margin, spread_tolerance_mm, probe_distance


def _move_to_fixed_sensor(executor, *, bit_setter_x_mm: float, bit_setter_y_mm: float, probe_z_location: float) -> None:
    executor._macro_send("G21")
    executor._macro_send("M5")
    executor._macro_send("G90")
    executor._macro_send(f"G53 G0 Z{_SAFE_MACHINE_Z_MM}")
    executor._macro_send(f"G53 G0 X{bit_setter_x_mm} Y{bit_setter_y_mm}")
    _safe_wait_for_idle(executor)
    executor._macro_send(f"G53 Z{probe_z_location}")


def _return_from_fixed_sensor(executor) -> None:
    executor._macro_send("G91")
    executor._macro_send(f"G0 Z{_TOOL_SENSOR_RETRACT_MM}")
    executor._macro_send("G90")
    executor._macro_send(f"G53 Z{_SAFE_MACHINE_Z_MM}")
    _safe_wait_for_idle(executor)
    executor._macro_send("G0 X0 Y0")


def run_park_work(executor) -> None:
    _safe_wait_for_idle(executor)
    executor._macro_send("M5")
    executor._macro_send("G21")
    executor._macro_send("G90")
    executor._macro_send(f"G53 G0 Z{_SAFE_MACHINE_Z_MM}")
    _safe_wait_for_idle(executor)
    executor._macro_send("G0 X0 Y0")
    executor._workflow_restore_state()


def run_park_bit_setter(executor) -> None:
    _safe_wait_for_idle(executor)
    cycle = tool_measurement.tool_probe_cycle_settings(executor.app)
    executor._macro_send("M5")
    executor._macro_send("G21")
    executor._macro_send("G90")
    executor._macro_send(f"G53 G0 Z{_SAFE_MACHINE_Z_MM}")
    executor._macro_send(f"G53 G0 X{cycle.bit_setter_x_mm} Y{cycle.bit_setter_y_mm}")
    _safe_wait_for_idle(executor)
    executor._workflow_restore_state()


def _touchplate_probe_job_setup(executor, *, setup_mode: str, z_max_travel: float, probe_safety_margin: float) -> None:
    xyz_settings = tool_measurement.xyz_plate_settings(executor.app)
    _set_state(executor, "XYZ_PLATE_SETTINGS", xyz_settings)
    _set_state(executor, "PLATE_THICKNESS", max(0.001, float(xyz_settings.plate_thickness_mm)))
    _set_state(
        executor,
        "TOUCHPLATE_MIN_SAFE_PROBE_DISTANCE",
        max(0.001, float(xyz_settings.min_safe_probe_distance_mm)),
    )
    _set_state(executor, "X_PLATE_OFFSET", float(xyz_settings.x_offset_mm))
    _set_state(executor, "Y_PLATE_OFFSET", float(xyz_settings.y_offset_mm))
    _set_state(
        executor,
        "XYZ_PLATE_SIDE_CLEARANCE_DISTANCE",
        max(0.001, float(xyz_settings.side_clearance_distance_mm)),
    )
    _set_state(executor, "XYZ_PLATE_Z_ROUGH_FEEDRATE", max(0.001, float(xyz_settings.z_rough_probe_feed_mm_min)))
    _set_state(executor, "XYZ_PLATE_Z_REPROBE_FEEDRATE", max(0.001, float(xyz_settings.z_reprobe_feed_mm_min)))
    _set_state(executor, "XYZ_PLATE_Z_FINE_FEEDRATE", max(0.001, float(xyz_settings.z_fine_probe_feed_mm_min)))
    _set_state(executor, "XYZ_PLATE_XY_ROUGH_FEEDRATE", max(0.001, float(xyz_settings.xy_rough_probe_feed_mm_min)))
    _set_state(executor, "XYZ_PLATE_XY_FINE_FEEDRATE", max(0.001, float(xyz_settings.xy_fine_probe_feed_mm_min)))
    _set_state(executor, "XYZ_PLATE_PROBE_DWELL_S", max(0.0, float(xyz_settings.dwell_s)))
    executor._macro_send("G90")
    executor._macro_send("G21")
    executor._macro_send("G92 X0 Y0")
    _wait_for_fresh_status(executor)
    current_mz = float(_get_var(executor, "mz", 0.0))
    _set_state(executor, "TOUCHPLATE_CURRENT_MZ", current_mz)
    _set_state(executor, "TOUCHPLATE_SOFT_LIMIT_FLOOR_MZ", -float(z_max_travel))
    z_fast_probe_distance = round(float(z_max_travel) + current_mz - float(probe_safety_margin), 3)
    _set_state(executor, "Z_FAST_PROBE_DISTANCE", z_fast_probe_distance)
    if z_fast_probe_distance < float(_get_state(executor, "TOUCHPLATE_MIN_SAFE_PROBE_DISTANCE", 1.0)):
        raise RuntimeError(
            f"Job Setup touchplate probing cannot start safely because machine Z ({current_mz:.3f} mm) is too low. Raise Z and try again."
        )
    _set_state(executor, "TOUCHPLATE_SAFE_REMAINING_DOWNWARD_TRAVEL", z_fast_probe_distance)
    executor.ui_q.put(
        (
            "log",
            "[workflow] "
            f"Touchplate fast probe planning: z_max_travel={z_max_travel} current_machine_z={current_mz} "
            f"soft_limit_floor_mz={-float(z_max_travel)} probe_safety_margin={probe_safety_margin} "
            f"min_safe_probe_distance={_get_state(executor, 'TOUCHPLATE_MIN_SAFE_PROBE_DISTANCE')} "
            f"computed_fast_probe_distance={z_fast_probe_distance}",
        )
    )
    executor.ui_q.put(
        (
            "log",
            "[workflow] "
            f"Touchplate settings: thickness={_get_state(executor, 'PLATE_THICKNESS')} "
            f"min_safe_probe_distance={_get_state(executor, 'TOUCHPLATE_MIN_SAFE_PROBE_DISTANCE')} "
            f"side_clearance_distance={_get_state(executor, 'XYZ_PLATE_SIDE_CLEARANCE_DISTANCE')} "
            f"z_rough_feed={_get_state(executor, 'XYZ_PLATE_Z_ROUGH_FEEDRATE')} "
            f"z_reprobe_feed={_get_state(executor, 'XYZ_PLATE_Z_REPROBE_FEEDRATE')} "
            f"z_fine_feed={_get_state(executor, 'XYZ_PLATE_Z_FINE_FEEDRATE')} "
            f"xy_rough_feed={_get_state(executor, 'XYZ_PLATE_XY_ROUGH_FEEDRATE')} "
            f"xy_fine_feed={_get_state(executor, 'XYZ_PLATE_XY_FINE_FEEDRATE')} "
            f"dwell_s={_get_state(executor, 'XYZ_PLATE_PROBE_DWELL_S')} "
            f"x_offset={_get_state(executor, 'X_PLATE_OFFSET')} "
            f"y_offset={_get_state(executor, 'Y_PLATE_OFFSET')}",
        )
    )
    executor._macro_send("G91")
    executor._macro_send(f"G38.2 Z-{z_fast_probe_distance} F{_get_state(executor, 'XYZ_PLATE_Z_ROUGH_FEEDRATE')}")
    executor._macro_send("G90")
    executor._macro_send(f"G92 Z{_get_state(executor, 'PLATE_THICKNESS')}")
    executor._macro_send(f"G1 Z{_TOUCHPLATE_CLEARANCE_RETRACT_MM}")
    executor._macro_send("G91")
    executor._macro_send(f"G38.2 Z-15 F{_get_state(executor, 'XYZ_PLATE_Z_REPROBE_FEEDRATE')}")
    executor._macro_send(f"G0 Z{_TOUCHPLATE_RETRACT_MM}")
    executor._macro_send(f"G4 P{_get_state(executor, 'XYZ_PLATE_PROBE_DWELL_S')}")
    executor._macro_send(f"G38.2 Z-{_TOUCHPLATE_FINE_PROBE_DISTANCE_MM} F{_get_state(executor, 'XYZ_PLATE_Z_FINE_FEEDRATE')}")
    executor._macro_send("G90")
    executor._macro_send(f"G92 Z{_get_state(executor, 'PLATE_THICKNESS')}")
    executor._macro_send(f"G0 Z{_TOUCHPLATE_CLEAR_Z_MM}")

    if str(setup_mode) == "xyz":
        executor._macro_send(f"G0 X-{_get_state(executor, 'XYZ_PLATE_SIDE_CLEARANCE_DISTANCE')} F800")
        executor._macro_send("G0 Z4")
        executor._macro_send(f"G38.2 X0 F{_get_state(executor, 'XYZ_PLATE_XY_ROUGH_FEEDRATE')}")
        executor._macro_send(f"G92 X{_get_state(executor, 'X_PLATE_OFFSET')}")
        executor._macro_send(f"G1 X{_XYZ_RETRACT_X_MM}")
        executor._macro_send(f"G38.2 X0 F{_get_state(executor, 'XYZ_PLATE_XY_FINE_FEEDRATE')}")
        executor._macro_send(f"G92 X{_get_state(executor, 'X_PLATE_OFFSET')}")
        executor._macro_send(f"G0 X{_XYZ_CLEAR_X_MM}")
        executor._macro_send(f"G0 Z{_TOUCHPLATE_CLEAR_Z_MM}")
        executor._macro_send(f"G0 X{_XYZ_SECOND_SIDE_X_MM} Y-{_get_state(executor, 'XYZ_PLATE_SIDE_CLEARANCE_DISTANCE')} F800")
        executor._macro_send("G0 Z4")
        executor._macro_send(f"G38.2 Y0 F{_get_state(executor, 'XYZ_PLATE_XY_ROUGH_FEEDRATE')}")
        executor._macro_send(f"G92 Y{_get_state(executor, 'Y_PLATE_OFFSET')}")
        executor._macro_send(f"G1 Y{_XYZ_RETRACT_Y_MM}")
        executor._macro_send(f"G38.2 Y0 F{_get_state(executor, 'XYZ_PLATE_XY_FINE_FEEDRATE')}")
        executor._macro_send(f"G92 Y{_get_state(executor, 'Y_PLATE_OFFSET')}")
        executor._macro_send(f"G0 Y{_XYZ_CLEAR_Y_MM}")

    executor._macro_send(f"G0 Z{_TOUCHPLATE_FINAL_Z_MM}")
    executor._macro_send("G0 X0 Y0")
    _wait_for_fresh_status(executor)
    if str(setup_mode) == "manual":
        note = "Manual mode selected: keeping existing XYZ zero and capturing reference only."
    else:
        note = (
            f"After touch plate: wx={_get_var(executor, 'wx')} wy={_get_var(executor, 'wy')} "
            f"wz={_get_var(executor, 'wz')}"
        )
    executor.ui_q.put(("log", f"[workflow] {note}"))


def run_job_setup(executor) -> None:
    _safe_wait_for_idle(executor)
    _log_work_position_snapshot(executor, "Job Setup start")
    probe_cycle = tool_measurement.tool_probe_cycle_settings(executor.app)
    z_max_travel = _grbl_z_max_travel(executor, "Job Setup")
    probe_z_location = _read_float_setting(executor, "macro_probe_z_location", -5.0)
    probe_safety_margin = max(0.0, _read_float_setting(executor, "macro_probe_safety_margin", 3.0))
    spread_tolerance_mm = max(
        0.001,
        _read_float_setting(
            executor,
            "macro_tool_probe_spread_tolerance_mm",
            tool_measurement.DEFAULT_TOOL_PROBE_SPREAD_TOLERANCE_MM,
        ),
    )
    selection = executor._workflow_prompt(
        "Job Setup",
        "Choose setup type.",
        ["XYZ Plate", "Z Plate", "Manual"],
        button_keys={"XYZ Plate": "x", "Z Plate": "z", "Manual": "m"},
    )
    choice_key = str(selection.get("key") or "").strip().lower()
    setup_mode = {"x": "xyz", "z": "z", "m": "manual"}.get(choice_key, "")
    if not setup_mode:
        raise RuntimeError("Job Setup mode not selected.")
    _set_state(executor, "SETUP_MODE", setup_mode)
    executor.ui_q.put(("log", f"[workflow] Job Setup mode selected: {setup_mode}"))

    existing_reference = _get_state(executor, "TOOL_REFERENCE", None)
    if existing_reference is not None:
        executor._workflow_prompt(
            "Job Setup",
            "You are about to overwrite material and tool reference points. Are you sure you want to proceed?",
            ["Proceed"],
        )

    if setup_mode == "xyz":
        executor._workflow_prompt(
            "Job Setup",
            "XYZ Plate mode: ensure the touch plate is installed and the clip is attached to the bit.",
            ["Resume"],
        )
    elif setup_mode == "z":
        executor._workflow_prompt(
            "Job Setup",
            "Z Plate mode: ensure the touch plate is installed for Z probing and the clip is attached.",
            ["Resume"],
        )
    else:
        executor._workflow_prompt(
            "Job Setup",
            "Manual mode: confirm XYZ work zero is already set.",
            ["Resume"],
        )

    if setup_mode in ("xyz", "z"):
        _touchplate_probe_job_setup(
            executor,
            setup_mode=setup_mode,
            z_max_travel=z_max_travel,
            probe_safety_margin=probe_safety_margin,
        )

    followup_message = (
        "Stow the touch plate and clip; tool height will be measured next."
        if setup_mode in ("xyz", "z")
        else "Proceeding to fixed sensor to capture tool reference."
    )
    executor._workflow_prompt("Job Setup", followup_message, ["Resume"])

    probe_distance = max(1.0, round(z_max_travel + probe_z_location - probe_safety_margin, 3))
    _move_to_fixed_sensor(
        executor,
        bit_setter_x_mm=probe_cycle.bit_setter_x_mm,
        bit_setter_y_mm=probe_cycle.bit_setter_y_mm,
        probe_z_location=probe_z_location,
    )
    executor.ui_q.put(
        (
            "log",
            "[workflow][tool] "
            f"Tool reference capture: fixed sensor ready at X={probe_cycle.bit_setter_x_mm} "
            f"Y={probe_cycle.bit_setter_y_mm} Z={probe_z_location}; "
            f"rough_feed={probe_cycle.rough_probe_feed_mm_min} fine_feed={probe_cycle.fine_probe_feed_mm_min} "
            f"dwell_s={probe_cycle.dwell_s} tolerance_mm={spread_tolerance_mm}.",
        )
    )
    measurement = executor.measure_tool_probe_machine_z(
        probe_distance_mm=probe_distance,
        rapid_feed_mm_min=float(probe_cycle.rough_probe_feed_mm_min),
        spread_tolerance_mm=spread_tolerance_mm,
        measurement_label="Tool reference measurement",
        allow_tool_change_retry=False,
        log=executor._workflow_log,
    )
    _set_state(executor, "TOOL_REFERENCE_MEASUREMENT", measurement)
    _set_state(executor, "TOOL_REFERENCE_MZ", round(float(measurement.machine_z), 4))
    _set_state(executor, "TOOL_REFERENCE_SPREAD_MM", round(float(measurement.spread_mm), 4))
    _safe_wait_for_idle(executor)
    _wait_for_fresh_status(executor)
    tool_reference_wz = round(float(_get_var(executor, "wz", 0.0)), 4)
    _set_state(executor, "TOOL_REFERENCE_WZ", tool_reference_wz)
    _set_state(executor, "TOOL_REFERENCE", tool_reference_wz)
    _set_state(executor, "TOOL_REFERENCE_FORMAT", tool_measurement.CURRENT_TOOL_REFERENCE_FORMAT)
    executor.ui_q.put(
        (
            "log",
            "[workflow] "
            f"Tool reference capture committed: stored TOOL_REFERENCE_WZ={tool_reference_wz} "
            f"TOOL_REFERENCE_MZ={_get_state(executor, 'TOOL_REFERENCE_MZ')} "
            f"as TOOL_REFERENCE={tool_reference_wz} format={tool_measurement.CURRENT_TOOL_REFERENCE_FORMAT} "
            f"spread_mm={_get_state(executor, 'TOOL_REFERENCE_SPREAD_MM')} "
            f"mz={_get_var(executor, 'mz')} wz={_get_var(executor, 'wz')}",
        )
    )
    _return_from_fixed_sensor(executor)
    executor._workflow_restore_state()


def run_tool_change(executor) -> None:
    _safe_wait_for_idle(executor)
    probe_cycle = tool_measurement.tool_probe_cycle_settings(executor.app)
    probe_z_location = _read_float_setting(executor, "macro_probe_z_location", -5.0)
    probe_safety_margin = max(0.0, _read_float_setting(executor, "macro_probe_safety_margin", 3.0))
    spread_tolerance_mm = max(
        0.001,
        _read_float_setting(
            executor,
            "macro_tool_probe_spread_tolerance_mm",
            tool_measurement.DEFAULT_TOOL_PROBE_SPREAD_TOLERANCE_MM,
        ),
    )
    review_threshold_mm = max(
        1.0,
        _read_float_setting(
            executor,
            "macro_tool_change_review_threshold_mm",
            tool_measurement.DEFAULT_TOOL_CHANGE_REVIEW_THRESHOLD_MM,
        ),
    )
    z_max_travel = _grbl_z_max_travel(executor, "Tool Change")
    probe_distance = max(1.0, round(z_max_travel + probe_z_location - probe_safety_margin, 3))
    if _get_state(executor, "TOOL_REFERENCE", None) is None:
        raise RuntimeError("TOOL_REFERENCE not set; run Job Setup first.")
    reference_format = str(_get_state(executor, "TOOL_REFERENCE_FORMAT", "") or "").strip()
    if reference_format != tool_measurement.CURRENT_TOOL_REFERENCE_FORMAT:
        raise RuntimeError(
            "Tool reference is missing the current sensor-reference format; rerun Job Setup before changing tools."
        )
    _set_state(
        executor,
        "TOOL_REFERENCE_WZ",
        round(float(_get_state(executor, "TOOL_REFERENCE_WZ", _get_state(executor, "TOOL_REFERENCE"))), 4),
    )
    _set_state(
        executor,
        "TOOL_REFERENCE_MZ",
        round(float(_get_state(executor, "TOOL_REFERENCE_MZ", _get_state(executor, "TOOL_REFERENCE"))), 4),
    )
    _log_work_position_snapshot(executor, "Tool change start", include_tool_reference=True)
    _move_to_fixed_sensor(
        executor,
        bit_setter_x_mm=probe_cycle.bit_setter_x_mm,
        bit_setter_y_mm=probe_cycle.bit_setter_y_mm,
        probe_z_location=probe_z_location,
    )
    context = str(_get_state(executor, "TOOL_CHANGE_CONTEXT", _get_var(executor, "tool_change_context", "")) or "").strip().lower()
    required_tool_name = str(
        _get_state(executor, "REQUIRED_TOOL_NAME", _get_var(executor, "tool_change_required_tool_name", ""))
        or ""
    ).strip()
    if context == "stream" and required_tool_name:
        prompt_message = (
            f"Please remove current tool and replace with {required_tool_name}. "
            "Press Resume once tool is swapped or Cancel to end job."
        )
    else:
        prompt_message = "Please swap tool. Press Resume once tool is swapped or Cancel to abort."
    executor._workflow_prompt("Tool Change", prompt_message, ["Resume"])
    executor.ui_q.put(
        (
            "log",
            "[workflow][tool] "
            f"Tool change measurement: fixed sensor ready at X={probe_cycle.bit_setter_x_mm} "
            f"Y={probe_cycle.bit_setter_y_mm} Z={probe_z_location}; "
            f"rough_feed={probe_cycle.rough_probe_feed_mm_min} fine_feed={probe_cycle.fine_probe_feed_mm_min} "
            f"dwell_s={probe_cycle.dwell_s} reference_mz={_get_state(executor, 'TOOL_REFERENCE_MZ')} "
            f"tolerance_mm={spread_tolerance_mm}.",
        )
    )
    measurement = executor.measure_tool_probe_machine_z(
        probe_distance_mm=probe_distance,
        rapid_feed_mm_min=float(probe_cycle.rough_probe_feed_mm_min),
        spread_tolerance_mm=spread_tolerance_mm,
        measurement_label="Tool change measurement",
        allow_tool_change_retry=True,
        log=executor._workflow_log,
    )
    _set_state(executor, "CURRENT_TOOL_MEASUREMENT", measurement)
    _set_state(executor, "CURRENT_TOOL_PROBE_MZ", round(float(measurement.machine_z), 4))
    _set_state(executor, "CURRENT_TOOL_PROBE_SPREAD_MM", round(float(measurement.spread_mm), 4))
    _safe_wait_for_idle(executor)
    _wait_for_fresh_status(executor)
    compensation = tool_measurement.build_tool_change_compensation(
        reference_work_z=float(_get_state(executor, "TOOL_REFERENCE_WZ")),
        reference_machine_z=float(_get_state(executor, "TOOL_REFERENCE_MZ")),
        current_machine_z=float(_get_state(executor, "CURRENT_TOOL_PROBE_MZ")),
    )
    delta_mm = round(float(compensation.delta_mm), 4)
    target_wz = round(float(compensation.target_work_z), 4)
    review_required = bool(tool_measurement.delta_exceeds_review_limit(delta_mm, review_threshold_mm))
    _set_state(executor, "TOOL_CHANGE_COMPENSATION", compensation)
    _set_state(executor, "TOOL_LENGTH_DELTA_MM", delta_mm)
    _set_state(executor, "TOOL_TARGET_WZ", target_wz)
    _set_state(executor, "MAX_TOOL_LENGTH_DELTA_MM", review_threshold_mm)
    _set_state(executor, "REVIEW_REQUIRED", review_required)
    executor.ui_q.put(
        (
            "log",
            "[workflow] "
            f"Tool change calculation: reference_wz={_get_state(executor, 'TOOL_REFERENCE_WZ')} "
            f"reference_mz={_get_state(executor, 'TOOL_REFERENCE_MZ')} current_mz={_get_state(executor, 'CURRENT_TOOL_PROBE_MZ')} "
            f"current_wz_observed={_get_var(executor, 'wz')} delta_mm={delta_mm} target_wz={target_wz} "
            f"review_threshold_mm={review_threshold_mm} review_required={review_required}",
        )
    )
    if review_required:
        executor._workflow_log(
            f"Tool change review required: delta_mm={delta_mm} exceeds threshold_mm={review_threshold_mm}; waiting for operator Apply/Cancel before offset write."
        )
        executor._workflow_prompt(
            "Tool Change Review",
            (
                f"The measured tool-length change is {delta_mm} mm, which exceeds the automatic review "
                f"threshold of {review_threshold_mm} mm. Press Apply to commit this offset or Cancel to abort "
                "before the offset write."
            ),
            ["Apply"],
        )
        executor._workflow_log("Tool change review accepted by operator; continuing with offset write.")
    executor.ui_q.put(("log", f"[workflow] Applying tool-change offset write: G10 L20 Z{target_wz}"))
    executor._macro_send(f"G10 L20 Z{target_wz}")
    _safe_wait_for_idle(executor)
    _wait_for_fresh_status(executor)
    executor.ui_q.put(
        (
            "log",
            "[workflow] "
            f"Tool change offset committed: G10 L20 applied; target_wz={target_wz} resulting_wz={_get_var(executor, 'wz')} mz={_get_var(executor, 'mz')}",
        )
    )
    _return_from_fixed_sensor(executor)
    executor._workflow_restore_state()


_WORKFLOW_RUNNERS = {
    "park_work": run_park_work,
    "park_bit_setter": run_park_bit_setter,
    "job_setup": run_job_setup,
    "tool_change": run_tool_change,
}


def execute_builtin_workflow(executor, workflow_id: str) -> None:
    try:
        runner = _WORKFLOW_RUNNERS[str(workflow_id)]
    except KeyError as exc:
        raise KeyError(f"Unknown built-in workflow: {workflow_id}") from exc
    runner(executor)
