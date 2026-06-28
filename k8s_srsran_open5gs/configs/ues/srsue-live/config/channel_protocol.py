#!/usr/bin/env python3

import json
import math
from dataclasses import dataclass


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
MAX_TAPS = 48
MAX_DELAY = 255
VALID_DIRECTIONS = {"both", "downlink", "uplink"}


@dataclass(frozen=True)
class Tap:
    delay: int
    coefficient: complex


@dataclass(frozen=True)
class ChannelUpdate:
    sequence: int
    direction: str
    activate_at_sample: int
    taps: tuple
    client_send_ns: int


def strict_integer(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def validate_taps(taps):
    if not taps:
        raise ValueError("at least one tap is required")

    combined = {}
    for index, tap in enumerate(taps):
        if not isinstance(tap, Tap):
            raise ValueError(f"tap {index} is invalid")
        delay = strict_integer(tap.delay, f"tap {index} delay")
        if delay < 0 or delay > MAX_DELAY:
            raise ValueError(
                f"tap delay {delay} is outside the allowed range "
                f"0..{MAX_DELAY}"
            )
        coefficient = complex(tap.coefficient)
        if not (
            math.isfinite(coefficient.real)
            and math.isfinite(coefficient.imag)
        ):
            raise ValueError("tap coefficients must be finite")
        combined[delay] = combined.get(delay, 0.0j) + coefficient

    validated = tuple(
        Tap(delay=delay, coefficient=coefficient)
        for delay, coefficient in sorted(combined.items())
        if coefficient != 0.0j
    )
    if not validated:
        raise ValueError("combined taps cannot all be zero")
    if len(validated) > MAX_TAPS:
        raise ValueError(f"at most {MAX_TAPS} unique taps are supported")
    return validated


def tap_objects(raw_taps):
    if not isinstance(raw_taps, list) or not raw_taps:
        raise ValueError("taps must be a non-empty list")
    taps = []
    for index, raw in enumerate(raw_taps):
        if not isinstance(raw, dict):
            raise ValueError(f"tap {index} must be an object")
        if set(raw) != {"delay", "real", "imag"}:
            raise ValueError(
                f"tap {index} must contain delay, real, and imag"
            )
        delay = strict_integer(raw["delay"], f"tap {index} delay")
        real = float(raw["real"])
        imag = float(raw["imag"])
        taps.append(Tap(delay, complex(real, imag)))
    return validate_taps(taps)


def parse_update(message):
    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")
    if message.get("version") != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    if message.get("msg_type") != "channel_update":
        raise ValueError("message type must be channel_update")

    sequence = strict_integer(message.get("sequence"), "sequence")
    if sequence < 0 or sequence > (1 << 64) - 1:
        raise ValueError("sequence is outside uint64 range")
    direction = message.get("direction")
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"invalid direction: {direction}")
    activate_at_sample = strict_integer(
        message.get("activate_at_sample"),
        "activate_at_sample",
    )
    if activate_at_sample < 0 or activate_at_sample > (1 << 64) - 1:
        raise ValueError("activate_at_sample is outside uint64 range")
    client_send_ns = strict_integer(
        message.get("client_send_ns", 0),
        "client_send_ns",
    )
    if client_send_ns < 0:
        raise ValueError("client_send_ns cannot be negative")

    allowed = {
        "version",
        "msg_type",
        "sequence",
        "direction",
        "activate_at_sample",
        "taps",
        "client_send_ns",
    }
    unknown = set(message) - allowed
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")

    return ChannelUpdate(
        sequence=sequence,
        direction=direction,
        activate_at_sample=activate_at_sample,
        taps=tap_objects(message.get("taps")),
        client_send_ns=client_send_ns,
    )


def build_update(
    taps,
    sequence,
    activate_at_sample,
    direction="both",
    client_send_ns=0,
):
    taps = validate_taps(tuple(taps))
    message = {
        "version": PROTOCOL_VERSION,
        "msg_type": "channel_update",
        "sequence": strict_integer(sequence, "sequence"),
        "direction": direction,
        "activate_at_sample": strict_integer(
            activate_at_sample,
            "activate_at_sample",
        ),
        "taps": [
            {
                "delay": tap.delay,
                "real": tap.coefficient.real,
                "imag": tap.coefficient.imag,
            }
            for tap in taps
        ],
        "client_send_ns": strict_integer(client_send_ns, "client_send_ns"),
    }
    parse_update(message)
    return message


def decode_message(payload):
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError("payload must be bytes")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds maximum size")

    def reject_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    message = json.loads(
        payload.decode("utf-8"),
        parse_constant=reject_constant,
    )
    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")
    return message


def encode_message(message):
    payload = json.dumps(
        message,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds maximum size")
    return payload


def identity_taps():
    return (Tap(0, 1.0 + 0.0j),)


def attenuation_taps():
    return (Tap(0, 0.5011872336272722 + 0.0j),)


def safe_multipath_taps():
    return (
        Tap(0, 0.92 + 0.0j),
        Tap(12, 0.176 + 0.064j),
        Tap(40, 0.064 - 0.096j),
    )
