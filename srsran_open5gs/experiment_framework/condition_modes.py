#!/usr/bin/env python3


from .settings import PORT_FORWARD, PORT_FORWARD_STREAM


def _port_forward_display():
    mappings = []
    for value in (PORT_FORWARD, PORT_FORWARD_STREAM):
        if value and value not in mappings:
            mappings.append(value)
    return ", ".join(mappings) or "configured control port"


def condition_plan(condition, parameters=None):
    parameters = parameters or {}
    port_forward = _port_forward_display()
    propagation = condition.get("propagation", {})
    effects = ", ".join(f"{key}={value}" for key, value in sorted(propagation.items())) or "all RT effects off"
    actions = [
        "apply separate overlay",
        "confirm wrapper started no radio process",
        "start GNU Radio, gNB and UE once",
        "wait for random access, RRC and PDU session",
        "start CPU, GPU, AMF and process-identity monitoring",
        f"resolve scene {condition['scene']} with propagation {effects}",
        f"start kubectl port-forward on {port_forward}",
    ]
    return actions + [
        "run and validate complete moving-channel dry run",
        "activate position zero before establishing movement epoch",
        "run trajectory positions at fixed targets without restarts",
        "skip late positions and record continuous ping",
    ]


def study_plan(resolved_study):
    if not resolved_study["conditions"]:
        return [
            "record provenance and configured parameters",
            "no configured condition runs; live radio is not started",
            "write empty summary tables and checksums",
        ]
    actions = [
        "save original UE deployment, ConfigMap, image, pull policy and replicas",
        "start one continuous AMF monitor for the complete pilot",
    ]
    for condition in resolved_study["conditions"]:
        actions.append({
            "condition_id": condition["condition_id"],
            "trial_count": resolved_study["trials_per_condition"],
            "actions": condition_plan(condition, resolved_study.get("parameters")),
            "after_success": "restore deployment and validate only; do not reconnect radio",
            "after_failure": "restore, run recovery check, then stop study",
        })
    actions.extend([
        "stop AMF monitor and verify no unsafe event occurred",
        "generate individual-trial tables, aggregate tables and SVG plots",
        "write checksums for the complete result tree",
    ])
    return actions
