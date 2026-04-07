#!/usr/bin/env python3
"""Protected built-in workflow action descriptors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinWorkflowAction:
    workflow_id: str
    label: str
    tooltip: str
    kb_id: str
    kind: str
    command_attr: str | None = None
    log_gcode: str | None = None
    operator_assisted: bool = False


BUILTIN_WORKFLOW_ACTIONS: tuple[BuiltinWorkflowAction, ...] = (
    BuiltinWorkflowAction(
        workflow_id="home",
        label="Home",
        tooltip="Run the homing cycle.",
        kb_id="home",
        kind="direct",
        command_attr="_start_homing",
        log_gcode="$H",
    ),
    BuiltinWorkflowAction(
        workflow_id="park_bit_setter",
        label="Park at Bit Setter",
        tooltip="Moves to the tool-height sensor so you can clean, inspect, or stage without altering offsets.",
        kb_id="builtin_park_bit_setter",
        kind="workflow",
    ),
    BuiltinWorkflowAction(
        workflow_id="job_setup",
        label="Job Setup",
        tooltip="Guided setup chooser that asks for XYZ Plate, Z Plate, or Manual, then captures the current tool reference.",
        kb_id="builtin_job_setup",
        kind="workflow",
        operator_assisted=True,
    ),
    BuiltinWorkflowAction(
        workflow_id="tool_change",
        label="Tool Change",
        tooltip="Moves to the fixed sensor, lets you swap the tool, then re-applies the stored reference height.",
        kb_id="builtin_tool_change",
        kind="workflow",
        operator_assisted=True,
    ),
    BuiltinWorkflowAction(
        workflow_id="park_work",
        label="Park at Work",
        tooltip="Raises to the safe height and then parks at the WCS origin without changing offsets.",
        kb_id="builtin_park_work",
        kind="workflow",
    ),
)

BUILTIN_WORKFLOW_BY_ID = {
    action.workflow_id: action for action in BUILTIN_WORKFLOW_ACTIONS
}


def builtin_workflow_action(workflow_id: str) -> BuiltinWorkflowAction:
    try:
        return BUILTIN_WORKFLOW_BY_ID[str(workflow_id)]
    except KeyError as exc:
        raise KeyError(f"Unknown built-in workflow action: {workflow_id}") from exc
