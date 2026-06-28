#!/usr/bin/env python3


def condition_plan(condition):
    mode = condition["mode"]
    common = [
        "apply separate overlay",
        "confirm wrapper started no radio process",
        "start GNU Radio, gNB and UE once",
        "wait for random access, RRC and PDU session",
        "start CPU, GPU, AMF and process-identity monitoring",
    ]
    if mode == "baseline":
        return [
            "reuse complete pre-pilot baseline as the baseline condition trial",
            "record attachment, UE IP, ping and resource measurements",
        ]
    if mode == "fixed_attenuation":
        return common + [
            "apply fixed 6 dB sample scaling in both directions",
            "record fixed tap and ping results",
        ]
    if mode == "fixed_multipath":
        return common + [
            "load verified 80 percent three-path profile",
            "record sparse taps, dense FIR and ping results",
        ]
    if mode == "stationary_sionna":
        return common + [
            "start kubectl port-forward on 5555",
            "run and validate stationary Sionna dry calculation",
            "send absolute taps and record ACK and activation timing",
            "record ping while the channel remains active",
        ]
    if mode == "controlled_noise":
        return common + [
            "start kubectl port-forward on 5555",
            "apply validated stationary Sionna channel",
            "calibrate signal power during active traffic",
            "freeze amplitudes for 30,25,20,15,10,5,0 dB",
            "stop at first sustained attachment loss",
            "return noise to zero before restoration",
        ]
    if mode == "moving_sionna":
        return common + [
            "start kubectl port-forward on 5555",
            "run and validate complete moving-channel dry run",
            "activate position zero before establishing movement epoch",
            "run positions 1-20 at fixed 50 ms targets without restarts",
            "skip late positions and record continuous ping",
        ]
    raise ValueError(f"unsupported mode: {mode}")


def study_plan(resolved_study):
    actions = [
        "save original UE deployment, ConfigMap, image, pull policy and replicas",
        "start one continuous AMF monitor for the complete pilot",
        "run one complete pre-pilot baseline check",
    ]
    for condition in resolved_study["conditions"]:
        actions.append({
            "condition_id": condition["condition_id"],
            "trial_count": resolved_study["trials_per_condition"],
            "actions": condition_plan(condition),
            "after_success": "restore deployment and validate only; do not reconnect radio",
            "after_failure": "restore, run immediate complete baseline check, then stop study",
        })
    actions.extend([
        "run one complete final baseline check",
        "stop AMF monitor and verify no unsafe event occurred",
        "generate individual-trial tables, aggregate tables and SVG plots",
        "write checksums for the complete result tree",
    ])
    return actions
