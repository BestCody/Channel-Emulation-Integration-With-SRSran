#!/usr/bin/env python3

import math
from dataclasses import asdict, dataclass


# mirror of the compiled sparse_channel_cc limits
MAX_TAPS = 48
MAX_DELAY = 255


@dataclass(frozen=True)
class Tap:
    delay: int
    coefficient: complex


def _complex_json(value):
    return {
        "real": float(value.real),
        "imag": float(value.imag),
    }


def _tap_json(tap):
    return {
        "delay": int(tap.delay),
        **_complex_json(tap.coefficient),
        "power": float(abs(tap.coefficient) ** 2),
    }


def _round_sample_delay(delay_seconds, sample_rate):
    return int(math.floor(delay_seconds * sample_rate + 0.5))


def convert_paths(
    delays,
    coefficients,
    sample_rate,
    *,
    max_taps=MAX_TAPS,
    max_delay=MAX_DELAY,
    late_policy="reject",
    normalization="none",
):
    if late_policy not in {"reject", "drop"}:
        raise ValueError("late_policy must be reject or drop")
    if normalization not in {"none", "unit_energy"}:
        raise ValueError(
            "normalization must be none or unit_energy"
        )
    sample_rate = float(sample_rate)
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("sample_rate must be finite and positive")
    if len(delays) != len(coefficients):
        raise ValueError("delay and coefficient counts must match")

    original_paths = []
    combined = {}
    errors = []
    late_path_power = 0.0

    for index, (delay_value, coefficient_value) in enumerate(
        zip(delays, coefficients)
    ):
        delay_seconds = float(delay_value)
        coefficient = complex(coefficient_value)
        power = float(abs(coefficient) ** 2)
        record = {
            "index": index,
            "delay_seconds": delay_seconds,
            "rounded_sample_delay": None,
            "coefficient": _complex_json(coefficient),
            "power": power,
            "status": "valid",
        }

        if (
            delay_seconds == -1.0
            and coefficient == 0.0j
        ):
            record["status"] = "sionna_padding"
            original_paths.append(record)
            continue
        if (
            not math.isfinite(delay_seconds)
            or not math.isfinite(coefficient.real)
            or not math.isfinite(coefficient.imag)
        ):
            record["status"] = "invalid"
            errors.append(f"path {index} contains a non-finite value")
            original_paths.append(record)
            continue
        if delay_seconds < 0:
            record["status"] = "invalid"
            errors.append(f"path {index} has a negative delay")
            original_paths.append(record)
            continue

        sample_delay = _round_sample_delay(
            delay_seconds,
            sample_rate,
        )
        record["rounded_sample_delay"] = sample_delay
        if sample_delay > max_delay:
            record["status"] = "late"
            late_path_power += power
            if late_policy == "reject":
                errors.append(
                    f"path {index} rounds to delay {sample_delay}, "
                    f"above maximum {max_delay}"
                )
            original_paths.append(record)
            continue

        combined[sample_delay] = (
            combined.get(sample_delay, 0.0j) + coefficient
        )
        original_paths.append(record)

    combined_taps = tuple(
        Tap(delay, coefficient)
        for delay, coefficient in sorted(combined.items())
        if coefficient != 0.0j
    )
    combined_power = float(
        sum(abs(tap.coefficient) ** 2 for tap in combined_taps)
    )

    ranked = sorted(
        combined_taps,
        key=lambda tap: (
            -(abs(tap.coefficient) ** 2),
            tap.delay,
        ),
    )
    retained = tuple(sorted(ranked[:max_taps], key=lambda tap: tap.delay))
    truncated = tuple(ranked[max_taps:])
    truncated_power = float(
        sum(abs(tap.coefficient) ** 2 for tap in truncated)
    )

    retained_power_before_normalization = float(
        sum(abs(tap.coefficient) ** 2 for tap in retained)
    )
    if normalization == "unit_energy" and retained:
        if retained_power_before_normalization <= 0:
            errors.append("channel energy is zero")
        else:
            scale = 1.0 / math.sqrt(
                retained_power_before_normalization
            )
            retained = tuple(
                Tap(tap.delay, tap.coefficient * scale)
                for tap in retained
            )

    if not combined_taps:
        errors.append("combined channel is empty")
    if not retained:
        errors.append("no taps remain after limiting")

    retained_power = float(
        sum(abs(tap.coefficient) ** 2 for tap in retained)
    )
    original_power = float(
        sum(
            path["power"]
            for path in original_paths
            if path["status"] in {"valid", "late"}
        )
    )

    return {
        "safe_to_send": not errors,
        "errors": errors,
        "sample_rate": sample_rate,
        "max_taps": max_taps,
        "max_delay": max_delay,
        "late_policy": late_policy,
        "normalization": normalization,
        "absolute_coefficients_preserved": normalization == "none",
        "original_paths": original_paths,
        "original_path_power": original_power,
        "combined_taps": [_tap_json(tap) for tap in combined_taps],
        "combined_power": combined_power,
        "retained_taps": [_tap_json(tap) for tap in retained],
        "retained_power_before_normalization":
            retained_power_before_normalization,
        "retained_power": retained_power,
        "discarded_power": late_path_power + truncated_power,
        "discarded_late_path_power": late_path_power,
        "discarded_tap_limit_power": truncated_power,
        "discarded_taps": [_tap_json(tap) for tap in truncated],
    }


def taps_from_report(report):
    return tuple(
        Tap(
            int(tap["delay"]),
            complex(float(tap["real"]), float(tap["imag"])),
        )
        for tap in report["retained_taps"]
    )
