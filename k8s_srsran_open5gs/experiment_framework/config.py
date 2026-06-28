#!/usr/bin/env python3

import copy
import hashlib
import json
import math
import pathlib
from datetime import datetime, timezone


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIRED_RESULT_ROOT = pathlib.Path(
    "/home/h3lou/sionna-srsran/results/stage8"
)
MODES = {
    "baseline",
    "fixed_attenuation",
    "fixed_multipath",
    "stationary_sionna",
    "controlled_noise",
    "moving_sionna",
}


class ConfigError(ValueError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path):
    path = pathlib.Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"could not read JSON configuration {path}: {error}") from error
    if not isinstance(value, dict):
        raise ConfigError(f"configuration must be a JSON object: {path}")
    return value


def require(value, expected, message):
    if value != expected:
        raise ConfigError(message)


def source_path(value, *, relative_to=None):
    path = pathlib.Path(value)
    if not path.is_absolute():
        path = (relative_to or REPO_ROOT) / path
    path = path.resolve()
    if not path.exists():
        raise ConfigError(f"referenced file does not exist: {path}")
    return path


def source_record(path):
    path = pathlib.Path(path).resolve()
    try:
        display = str(path.relative_to(REPO_ROOT))
    except ValueError:
        display = str(path)
    return {
        "path": display,
        "absolute_path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def validate_throughput(condition):
    throughput = condition.get("throughput")
    if not isinstance(throughput, dict):
        raise ConfigError("every condition requires a throughput object")
    if throughput.get("status") != "deferred":
        raise ConfigError("Stage 8 throughput must remain deferred")
    reason = str(throughput.get("reason", ""))
    if "verified" not in reason or "endpoint" not in reason:
        raise ConfigError("throughput deferral must identify the missing verified endpoint")


def validate_condition(condition, condition_path):
    if condition.get("schema_version") != 1:
        raise ConfigError(f"unsupported condition schema: {condition_path}")
    condition_id = condition.get("condition_id")
    if not isinstance(condition_id, str) or not condition_id:
        raise ConfigError("condition_id is required")
    mode = condition.get("mode")
    if mode not in MODES:
        raise ConfigError(f"unsupported condition mode: {mode}")
    validate_throughput(condition)

    if mode == "fixed_attenuation":
        attenuation = float(condition.get("attenuation_db", math.nan))
        expected = float(condition.get("expected_amplitude", math.nan))
        calculated = 10.0 ** (-attenuation / 20.0)
        if not math.isclose(expected, calculated, rel_tol=0.0, abs_tol=1e-15):
            raise ConfigError("fixed attenuation amplitude does not match dB value")
    if mode == "stationary_sionna" and condition.get("normalization") != "none":
        raise ConfigError("stationary Sionna pilot must preserve absolute coefficients")
    if mode == "moving_sionna" and condition.get("noise_enabled") is not False:
        raise ConfigError("moving Sionna pilot requires noise disabled")
    if mode in {"stationary_sionna", "controlled_noise", "moving_sionna"}:
        require(condition.get("port_forward"), "5555:5555", "live modes require port-forward 5555:5555")
    return condition


def add_artifact(condition, key, artifacts):
    value = condition.get(key)
    if value is None:
        return
    path = source_path(value, relative_to=REPO_ROOT)
    record = source_record(path)
    condition[f"{key}_resolved"] = record
    artifacts.append(record)


def resolve_condition(reference, study_path):
    condition_path = source_path(reference, relative_to=study_path.parent)
    condition = validate_condition(load_json(condition_path), condition_path)
    resolved = copy.deepcopy(condition)
    resolved["configuration"] = source_record(condition_path)

    profile_path = source_path(
        condition.get("measurement_profile"),
        relative_to=condition_path.parent,
    )
    profile = load_json(profile_path)
    if profile.get("schema_version") != 1:
        raise ConfigError(f"unsupported measurement profile: {profile_path}")
    resolved["measurement_profile_resolved"] = {
        "configuration": source_record(profile_path),
        "values": profile,
    }

    artifacts = [resolved["configuration"], source_record(profile_path)]
    for key in (
        "tap_profile",
        "known_stress_profile_excluded",
        "scene",
        "noise_profile",
        "noise_calibration",
        "trajectory",
    ):
        add_artifact(resolved, key, artifacts)
    resolved["input_artifacts"] = artifacts
    return resolved


def validate_study(study, study_path):
    if study.get("schema_version") != 1:
        raise ConfigError("unsupported study schema")
    if not study.get("study_id"):
        raise ConfigError("study_id is required")
    result_root = pathlib.Path(study.get("result_root", "")).resolve()
    if result_root != REQUIRED_RESULT_ROOT:
        raise ConfigError(f"result_root must be {REQUIRED_RESULT_ROOT}")
    if REPO_ROOT == result_root or REPO_ROOT in result_root.parents:
        raise ConfigError("generated results must be outside the Git repository")
    trials = study.get("trials_per_condition")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 1:
        raise ConfigError("trials_per_condition must be a positive integer")
    if study.get("pilot") and trials != 1:
        raise ConfigError("the Stage 8 pilot must use one trial per condition")

    baseline = study.get("baseline_policy", {})
    require(baseline.get("before_pilot"), "complete", "pilot requires one complete pre-baseline")
    require(
        baseline.get("after_successful_condition"),
        "restoration-validation-only",
        "successful conditions must use restoration validation only",
    )
    require(
        baseline.get("after_failed_condition"),
        "immediate-complete-baseline-and-stop",
        "failed conditions require immediate baseline testing",
    )
    require(baseline.get("after_pilot"), "complete", "pilot requires one complete final baseline")

    amf = study.get("amf_safety", {})
    require(amf.get("continuous"), True, "AMF monitoring must be continuous")
    require(amf.get("stop_on_restart"), True, "AMF restart must stop the study")
    require(amf.get("stop_on_identity_change"), True, "AMF identity change must stop the study")
    if float(amf.get("stop_at_limit_fraction", 0)) != 0.90:
        raise ConfigError("AMF stop fraction must be 0.90")
    if int(amf.get("stop_at_growth_bytes", 0)) != 128 * 1024 * 1024:
        raise ConfigError("AMF growth stop must be 128 MiB")

    reporting = study.get("reporting", {})
    require(reporting.get("show_individual_trials"), True, "individual trials must be shown")
    require(reporting.get("confidence_intervals"), False, "confidence intervals must be disabled")
    if reporting.get("throughput") != "deferred-no-verified-user-plane-endpoint":
        raise ConfigError("throughput must be explicitly deferred")
    references = study.get("conditions")
    if not isinstance(references, list) or not references:
        raise ConfigError("study requires at least one condition")
    return result_root


def load_and_resolve_study(path, *, resolved_at=None):
    study_path = pathlib.Path(path).resolve()
    study = load_json(study_path)
    result_root = validate_study(study, study_path)
    conditions = [resolve_condition(item, study_path) for item in study["conditions"]]
    identifiers = [item["condition_id"] for item in conditions]
    if len(identifiers) != len(set(identifiers)):
        raise ConfigError("condition identifiers must be unique")
    if set(item["mode"] for item in conditions) != MODES:
        raise ConfigError("pilot must contain exactly the six approved comparison modes")
    if identifiers[0] != "baseline":
        raise ConfigError("baseline must be the first pilot condition")

    timestamp = resolved_at or datetime.now(timezone.utc).isoformat()
    resolved = copy.deepcopy(study)
    resolved["resolved_at_utc"] = timestamp
    resolved["study_configuration"] = source_record(study_path)
    resolved["result_root"] = str(result_root)
    resolved["conditions"] = conditions
    resolved["trial_count"] = len(conditions) * study["trials_per_condition"]
    resolved["throughput"] = {
        "status": "deferred",
        "reason": "No verified user-plane throughput endpoint exists",
    }
    return resolved
