#!/usr/bin/env python3

import os
import pathlib
import re
import subprocess
import sys


DEFAULT_AMF_N3_ADDR = "10.10.3.200"
DEFAULT_GNB_ZMQ_ADDR = "10.10.3.231"
DEFAULT_UE_ZMQ_ADDR = "10.10.3.232"
DEFAULT_ZMQ_INTERFACE = "n3"
PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _env(name):
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_int(name, default):
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _interface_ipv4(name):
    try:
        output = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    for line in output.splitlines():
        parts = line.split()
        if "inet" in parts:
            cidr = parts[parts.index("inet") + 1]
            return cidr.split("/", 1)[0]
    return None


def _endpoint(env_name, addr, port):
    return _env(env_name) or f"tcp://{addr}:{port}"


def render_values():
    interface = _env("SRSRAN_ZMQ_INTERFACE") or DEFAULT_ZMQ_INTERFACE
    gnb_bind_addr = (
        _env("SRSRAN_GNB_N3_BIND_ADDR")
        or _interface_ipv4(interface)
        or _env("SRSRAN_GNB_ZMQ_ADDR")
        or DEFAULT_GNB_ZMQ_ADDR
    )
    gnb_zmq_addr = _env("SRSRAN_GNB_ZMQ_ADDR") or gnb_bind_addr
    ue_zmq_addr = _env("SRSRAN_UE_ZMQ_ADDR") or DEFAULT_UE_ZMQ_ADDR
    return {
        "SRSRAN_AMF_N3_ADDR":
            _env("SRSRAN_AMF_N3_ADDR") or DEFAULT_AMF_N3_ADDR,
        "SRSRAN_GNB_N3_BIND_ADDR": gnb_bind_addr,
        "SRSRAN_ZMQ_GNB_DOWNLINK_ENDPOINT": _endpoint(
            "SRSRAN_ZMQ_GNB_DOWNLINK_ENDPOINT",
            gnb_zmq_addr,
            _env_int("SRSRAN_ZMQ_GNB_DOWNLINK_PORT", 2000),
        ),
        "SRSRAN_ZMQ_GNB_UPLINK_ENDPOINT": _endpoint(
            "SRSRAN_ZMQ_GNB_UPLINK_ENDPOINT",
            ue_zmq_addr,
            _env_int("SRSRAN_ZMQ_GNB_UPLINK_PORT", 2001),
        ),
    }


def render_text(text):
    values = render_values()

    def replace(match):
        key = match.group(1)
        if key in values:
            return values[key]
        value = _env(key)
        if value is not None:
            return value
        raise KeyError(f"no value configured for {key}")

    return PLACEHOLDER.sub(replace, text)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: render_gnb_config.py TEMPLATE_PATH OUTPUT_PATH"
        )
    template = pathlib.Path(sys.argv[1])
    output = pathlib.Path(sys.argv[2])
    output.write_text(
        render_text(template.read_text(encoding="utf-8")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
