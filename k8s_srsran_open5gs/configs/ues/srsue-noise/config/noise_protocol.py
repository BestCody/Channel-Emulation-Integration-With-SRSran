#!/usr/bin/env python3

import math
from dataclasses import dataclass


PROTOCOL_VERSION = 1
MAXIMUM_NOISE_AMPLITUDE = 512.0
VALID_DIRECTIONS = {"both", "downlink", "uplink"}


@dataclass(frozen=True)
class NoiseUpdate:
    sequence: int
    direction: str
    amplitudes: dict
    client_send_ns: int


def strict_integer(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def validate_amplitude(value, field):
    amplitude = float(value)
    if not math.isfinite(amplitude):
        raise ValueError(f"{field} must be finite")
    if amplitude < 0.0:
        raise ValueError(f"{field} cannot be negative")
    if amplitude > MAXIMUM_NOISE_AMPLITUDE:
        raise ValueError(
            f"{field} {amplitude} exceeds maximum allowed "
            f"{MAXIMUM_NOISE_AMPLITUDE}"
        )
    return amplitude


def parse_noise_update(message):
    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")
    if message.get("version") != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    if message.get("msg_type") != "noise_update":
        raise ValueError("message type must be noise_update")

    sequence = strict_integer(
        message.get("noise_sequence"),
        "noise_sequence",
    )
    if sequence < 0 or sequence > (1 << 64) - 1:
        raise ValueError("noise_sequence is outside uint64 range")
    direction = message.get("direction")
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"invalid direction: {direction}")
    client_send_ns = strict_integer(
        message.get("client_send_ns", 0),
        "client_send_ns",
    )
    if client_send_ns < 0:
        raise ValueError("client_send_ns cannot be negative")

    raw_amplitudes = message.get("amplitudes")
    if not isinstance(raw_amplitudes, dict):
        raise ValueError("amplitudes must be an object")
    expected = (
        {"downlink", "uplink"}
        if direction == "both"
        else {direction}
    )
    if set(raw_amplitudes) != expected:
        raise ValueError(
            f"amplitudes must contain exactly {sorted(expected)}"
        )
    amplitudes = {
        name: validate_amplitude(value, f"{name} amplitude")
        for name, value in raw_amplitudes.items()
    }

    allowed = {
        "version",
        "msg_type",
        "noise_sequence",
        "direction",
        "amplitudes",
        "client_send_ns",
    }
    unknown = set(message) - allowed
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")

    return NoiseUpdate(
        sequence=sequence,
        direction=direction,
        amplitudes=amplitudes,
        client_send_ns=client_send_ns,
    )


def build_noise_update(
    sequence,
    amplitudes,
    direction="both",
    client_send_ns=0,
):
    message = {
        "version": PROTOCOL_VERSION,
        "msg_type": "noise_update",
        "noise_sequence": strict_integer(sequence, "noise_sequence"),
        "direction": direction,
        "amplitudes": dict(amplitudes),
        "client_send_ns": strict_integer(client_send_ns, "client_send_ns"),
    }
    parse_noise_update(message)
    return message
