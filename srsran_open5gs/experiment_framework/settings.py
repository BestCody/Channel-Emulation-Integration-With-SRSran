#!/usr/bin/env python3

import copy
import json
import os
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PARAMETER_FILE = REPO_ROOT / "experiments" / "benchmark-parameters.json"

# Fixed plumbing; not user-configurable, always wins
FIXED_CHANNEL = {
    "control_endpoint": "tcp://127.0.0.1:5555",
    "stream_endpoint": "tcp://127.0.0.1:5556",
    "port_forward": "5555:5555",
    "port_forward_stream": "5556:5556",
    "port_forward_host": "127.0.0.1",
}
FIXED_RADIO = {
    "flowgraph_process_pattern": "[m]ulti_ue_.*channel.py|[m]ulti_ue_scenario.py",
    "gnb_process_pattern": "[/]srsran/gnb",
    "ue_process_pattern": "[/]opt/srsRAN_4G/build/srsue/src/srsue",
    "start_gnb_script": "/srsran/config/start_gnb.sh",
    "start_gnu_script": "/srsran/config/start_gnu.sh",
    "start_ue_script": "/srsran/config/start_ue.sh",
    "tun_interface": "tun_srsue",
    "gateway": "10.41.0.1",
}


def _deep_merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_json(path):
    path = pathlib.Path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"parameter file must contain a JSON object: {path}")
    return value


def _set_nested(parameters, keys, value):
    current = parameters
    for key in keys[:-1]:
        current = current.setdefault(key, {})
    current[keys[-1]] = value


def _bool(value):
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"not a boolean: {value}")


ENVIRONMENT_OVERRIDES = {
    "SRSRAN_UE_NUMBER": (("radio", "ue_number"), int),
    "SIONNA_PYTHON": (("host_python",), str),
    "BENCHMARK_RESULT_ROOT": (("result_root",), str),
    "SIONNA_RANDOMIZE_POSITIONS": (("scene", "randomize_positions"), _bool),
    "SIONNA_PLACEMENT_SEED": (("scene", "placement_seed"), int),
}


def load_benchmark_parameters(*parameter_files, inline=None):
    parameters = {}
    sources = []
    if DEFAULT_PARAMETER_FILE.exists():
        parameters = _deep_merge(parameters, _load_json(DEFAULT_PARAMETER_FILE))
        sources.append(str(DEFAULT_PARAMETER_FILE))
    for path in parameter_files:
        if path:
            parameters = _deep_merge(parameters, _load_json(path))
            sources.append(str(pathlib.Path(path).resolve()))
    if inline:
        if not isinstance(inline, dict):
            raise ValueError("inline parameters must be a JSON object")
        parameters = _deep_merge(parameters, inline)
    for name, (keys, converter) in ENVIRONMENT_OVERRIDES.items():
        if name in os.environ:
            _set_nested(parameters, keys, converter(os.environ[name]))
    parameters.setdefault("host_python", sys.executable)
    # Fixed plumbing always wins; constants not knobs
    parameters["channel"] = {**parameters.get("channel", {}), **FIXED_CHANNEL}
    parameters["radio"] = {**parameters.get("radio", {}), **FIXED_RADIO}
    parameters["_parameter_sources"] = sources
    return parameters


def resolve_repo_path(value, *, repo_root=REPO_ROOT):
    path = pathlib.Path(str(value)).expanduser()
    if not path.is_absolute():
        path = pathlib.Path(repo_root) / path
    return path.resolve()


def parameter_sources(parameters):
    return tuple(parameters.get("_parameter_sources", ()))
