#!/usr/bin/env python3


def _port_forward_display(channel):
    mappings = []
    for key in ("port_forward", "port_forward_stream"):
        value = channel.get(key)
        if value is None or value == "":
            continue
        values = [value] if isinstance(value, str) else value
        for item in values:
            mapping = str(item)
            if mapping and mapping not in mappings:
                mappings.append(mapping)
    return ", ".join(mappings) or "configured control port"


def condition_plan(condition, parameters=None):
    parameters = parameters or {}
    port_forward = _port_forward_display(parameters.get("channel", {}))
    propagation = condition.get("propagation", {})
    effects = ", ".join(f"{key}={value}" for key, value in sorted(propagation.items())) or "scene defaults"
    actions = [
        "apply separate overlay",
        "confirm wrapper started no radio process",
        "start GNU Radio, gNB and UE once",
        "wait for random access, RRC and PDU session",
        "start CPU, GPU, AMF and process-identity monitoring",
        f"resolve scene {condition['scene']} with propagation {effects}",
        f"start kubectl port-forward on {port_forward}",
    ]
    if (condition.get("noise") or {}).get("enabled"):
        return actions + [
            "run and validate stationary Sionna dry calculation",
            "calibrate signal power during active traffic",
            "sweep configured SNR levels until sustained attachment loss",
            "return noise to zero before restoration",
        ]
    if condition.get("mobility") == "moving":
        return actions + [
            "run and validate complete moving-channel dry run",
            "activate position zero before establishing movement epoch",
            "run trajectory positions at fixed targets without restarts",
            "skip late positions and record continuous ping",
        ]
    return actions + [
        "run and validate stationary Sionna dry calculation",
        "send absolute taps and record ACK and activation timing",
        "record ping while the channel remains active",
    ]


def study_plan(resolved_study):
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
