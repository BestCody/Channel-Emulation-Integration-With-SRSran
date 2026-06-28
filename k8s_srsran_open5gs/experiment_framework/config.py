#!/usr/bin/env python3

import copy
import hashlib
import json
import math
import pathlib
from datetime import datetime, timezone

from .settings import load_benchmark_parameters, parameter_sources, resolve_repo_path


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODES = {
    "baseline",
    "fixed_attenuation",
    "fixed_multipath",
    "stationary_sionna",
    "controlled_noise",
    "moving_sionna",
}
LIVE_MODES = {"stationary_sionna", "controlled_noise", "moving_sionna"}


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


def source_path(value, *, relative_to=None):
    path = pathlib.Path(str(value)).expanduser()
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


def _throughput_defaults(parameters):
    return parameters.get("throughput", {})


def validate_throughput(condition, parameters):
    throughput = condition.get("throughput")
    if not isinstance(throughput, dict):
        raise ConfigError("every condition requires a throughput object")
    policy = _throughput_defaults(parameters)
    allowed = set(policy.get("allowed_statuses", ["deferred", "measured"]))
    status = throughput.get("status")
    if status not in allowed:
        raise ConfigError(
            f"throughput status {status!r} is not allowed by benchmark parameters"
        )
    if status == "deferred":
        reason = str(throughput.get("reason", "")).lower()
        for term in policy.get("deferred_reason_terms", []):
            if str(term).lower() not in reason:
                raise ConfigError(
                    "throughput deferral reason does not match benchmark parameters"
                )


def _format_launcher(condition, parameters):
    launcher = condition.get("launcher")
    if launcher is None:
        return
    values = {
        "ue_number": parameters.get("radio", {}).get("ue_number", 1),
    }
    try:
        condition["launcher"] = str(launcher).format(**values)
    except KeyError as error:
        raise ConfigError(f"unknown launcher parameter {error} in {condition['condition_id']}") from error


def validate_condition(condition, condition_path, parameters):
    if condition.get("schema_version") != 1:
        raise ConfigError(f"unsupported condition schema: {condition_path}")
    condition_id = condition.get("condition_id")
    if not isinstance(condition_id, str) or not condition_id:
        raise ConfigError("condition_id is required")
    mode = condition.get("mode")
    if mode not in MODES:
        raise ConfigError(f"unsupported condition mode: {mode}")
    validate_throughput(condition, parameters)
    _format_launcher(condition, parameters)

    if mode == "fixed_attenuation":
        attenuation = float(condition.get("attenuation_db", math.nan))
        expected = float(condition.get("expected_amplitude", math.nan))
        calculated = 10.0 ** (-attenuation / 20.0)
        if not math.isclose(expected, calculated, rel_tol=0.0, abs_tol=1e-15):
            raise ConfigError("fixed attenuation amplitude does not match dB value")

    channel = parameters.get("channel", {})
    if (
        mode == "stationary_sionna"
        and channel.get("require_absolute_coefficients", True)
        and condition.get("normalization", "none") != "none"
    ):
        raise ConfigError("stationary Sionna condition must preserve absolute coefficients")
    if mode == "moving_sionna":
        expected_noise = bool(channel.get("moving_noise_enabled", False))
        if bool(condition.get("noise_enabled", expected_noise)) != expected_noise:
            raise ConfigError("moving Sionna noise setting does not match benchmark parameters")
    if mode in LIVE_MODES:
        condition.setdefault("port_forward", channel.get("port_forward"))
    return condition


def add_artifact(condition, key, artifacts):
    value = condition.get(key)
    if value is None:
        return
    path = source_path(value, relative_to=REPO_ROOT)
    record = source_record(path)
    condition[f"{key}_resolved"] = record
    artifacts.append(record)


def resolve_condition(reference, study_path, parameters):
    condition_path = source_path(reference, relative_to=study_path.parent)
    condition = validate_condition(load_json(condition_path), condition_path, parameters)
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


def _result_root(study, parameters):
    raw = study.get("result_root") or parameters.get("result_root")
    if not raw:
        raise ConfigError("result_root must be provided by the study or benchmark parameters")
    return resolve_repo_path(raw, repo_root=REPO_ROOT)


def validate_study(study, study_path, parameters):
    if study.get("schema_version") != 1:
        raise ConfigError("unsupported study schema")
    if not study.get("study_id"):
        raise ConfigError("study_id is required")
    result_root = _result_root(study, parameters)
    if parameters.get("results_must_be_outside_repo", True):
        if REPO_ROOT == result_root or REPO_ROOT in result_root.parents:
            raise ConfigError("generated results must be outside the Git repository")
    trials = study.get("trials_per_condition")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 1:
        raise ConfigError("trials_per_condition must be a positive integer")
    study_policy = parameters.get("study", {})
    if study.get("pilot") and study_policy.get("enforce_pilot_single_trial") and trials != 1:
        raise ConfigError("pilot trial count does not match benchmark parameters")
    if not isinstance(study.get("baseline_policy", {}), dict):
        raise ConfigError("baseline_policy must be an object")
    if not isinstance(study.get("amf_safety", {}), dict):
        raise ConfigError("amf_safety must be an object")
    references = study.get("conditions")
    if not isinstance(references, list) or not references:
        raise ConfigError("study requires at least one condition")
    return result_root


def _parameter_files(study, study_path, cli_parameter_files):
    files = []
    for item in study.get("parameter_files", []):
        files.append(source_path(item, relative_to=study_path.parent))
    for item in cli_parameter_files or []:
        files.append(source_path(item, relative_to=pathlib.Path.cwd()))
    return files


def load_and_resolve_study(path, *, resolved_at=None, parameter_files=None):
    study_path = pathlib.Path(path).resolve()
    study = load_json(study_path)
    files = _parameter_files(study, study_path, parameter_files)
    try:
        parameters = load_benchmark_parameters(*files, inline=study.get("parameters"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ConfigError(f"could not load benchmark parameters: {error}") from error
    if isinstance(study.get("amf_safety"), dict):
        parameters["amf_safety"] = copy.deepcopy(study["amf_safety"])
    result_root = validate_study(study, study_path, parameters)
    conditions = [
        resolve_condition(item, study_path, parameters)
        for item in study["conditions"]
    ]
    identifiers = [item["condition_id"] for item in conditions]
    if len(identifiers) != len(set(identifiers)):
        raise ConfigError("condition identifiers must be unique")
    policy = parameters.get("study", {})
    if policy.get("require_all_modes"):
        required_modes = set(policy.get("required_modes", sorted(MODES)))
        if set(item["mode"] for item in conditions) != required_modes:
            raise ConfigError("conditions do not match required benchmark modes")
    if policy.get("baseline_first", True) and identifiers[0] != "baseline":
        raise ConfigError("baseline must be the first condition")

    timestamp = resolved_at or datetime.now(timezone.utc).isoformat()
    resolved = copy.deepcopy(study)
    resolved["resolved_at_utc"] = timestamp
    resolved["study_configuration"] = source_record(study_path)
    resolved["parameter_configurations"] = [
        source_record(item) for item in parameter_sources(parameters)
    ]
    resolved["parameters"] = parameters
    resolved["result_root"] = str(result_root)
    resolved["conditions"] = conditions
    resolved["trial_count"] = len(conditions) * study["trials_per_condition"]
    throughput = parameters.get("throughput", {})
    resolved["throughput"] = {
        "status": throughput.get("default_status", "deferred"),
        "reason": throughput.get("default_reason", "No verified user-plane throughput endpoint exists"),
    }
    return resolved
