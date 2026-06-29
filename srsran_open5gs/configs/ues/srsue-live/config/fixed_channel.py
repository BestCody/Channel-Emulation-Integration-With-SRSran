#!/usr/bin/env python3

import argparse
import configparser
import json
import math
from dataclasses import dataclass

from channel_protocol import MAX_TAPS, MAX_DELAY


@dataclass(frozen=True)
class Tap:
    delay: int
    coefficient: complex


def validate_attenuation_db(attenuation_db):
    attenuation_db = float(attenuation_db)
    if not math.isfinite(attenuation_db):
        raise ValueError("attenuation_db must be finite")
    if attenuation_db < 0:
        raise ValueError("attenuation_db cannot be negative")
    return attenuation_db


def db_to_amplitude(attenuation_db):
    attenuation_db = validate_attenuation_db(attenuation_db)
    return 10.0 ** (-attenuation_db / 20.0)


def fixed_attenuation_taps(attenuation_db):
    return (
        Tap(
            delay=0,
            coefficient=complex(db_to_amplitude(attenuation_db), 0.0),
        ),
    )


def scale_samples(samples, attenuation_db):
    amplitude = db_to_amplitude(attenuation_db)
    return tuple(complex(sample) * amplitude for sample in samples)


def combine_taps(taps, max_taps=MAX_TAPS, max_delay=MAX_DELAY):
    if not taps:
        raise ValueError("at least one tap is required")

    combined = {}
    for tap in taps:
        if isinstance(tap.delay, bool) or not isinstance(tap.delay, int):
            raise ValueError("tap delays must be integers")
        if tap.delay < 0 or tap.delay > max_delay:
            raise ValueError(
                f"tap delay {tap.delay} is outside the allowed range "
                f"0..{max_delay}"
            )

        coefficient = complex(tap.coefficient)
        if not (
            math.isfinite(coefficient.real)
            and math.isfinite(coefficient.imag)
        ):
            raise ValueError("tap coefficients must be finite")
        combined[tap.delay] = combined.get(tap.delay, 0.0j) + coefficient

    combined_taps = tuple(
        Tap(delay=delay, coefficient=coefficient)
        for delay, coefficient in sorted(combined.items())
        if coefficient != 0.0j
    )
    if not combined_taps:
        raise ValueError("combined taps cannot all be zero")
    if len(combined_taps) > max_taps:
        raise ValueError(f"at most {max_taps} unique taps are supported")
    return combined_taps


def load_taps_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    raw_taps = data.get("taps")
    if not isinstance(raw_taps, list) or not raw_taps:
        raise ValueError("tap file must contain a non-empty taps list")

    taps = []
    for index, raw_tap in enumerate(raw_taps):
        if not isinstance(raw_tap, dict):
            raise ValueError(f"tap {index} must be an object")
        try:
            delay = raw_tap["delay"]
            real = float(raw_tap["real"])
            imag = float(raw_tap["imag"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"tap {index} is invalid") from error
        taps.append(
            Tap(
                delay=delay,
                coefficient=complex(real, imag),
            )
        )
    return combine_taps(taps)


def sparse_to_dense(taps):
    combined_taps = combine_taps(taps)
    dense = [0.0j] * (combined_taps[-1].delay + 1)
    for tap in combined_taps:
        dense[tap.delay] = tap.coefficient
    return dense


def validate_sample_rate(sample_rate):
    sample_rate = float(sample_rate)
    if not math.isfinite(sample_rate):
        raise ValueError("sample rate must be finite")
    if sample_rate <= 0:
        raise ValueError("sample rate must be greater than zero")
    return sample_rate


def samples_per_symbol(sample_rate, scs_khz=15.0):
    # Average OFDM symbol length
    sample_rate = validate_sample_rate(sample_rate)
    scs_khz = float(scs_khz)
    if not math.isfinite(scs_khz) or scs_khz < 15.0:
        raise ValueError("scs_khz must be a numerology >= 15")
    mu = round(math.log2(scs_khz / 15.0))
    symbols_per_second = 14000.0 * (2 ** mu)
    return max(1, int(round(sample_rate / symbols_per_second)))


def sample_rate_from_ue_config(path):
    parser = configparser.ConfigParser()
    loaded = parser.read(path, encoding="utf-8")
    if not loaded:
        raise ValueError(f"could not read UE configuration: {path}")

    try:
        configured_rate = parser["rf"]["srate"]
    except KeyError as error:
        raise ValueError(
            f"UE configuration has no [rf] srate value: {path}"
        ) from error

    return validate_sample_rate(configured_rate)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fixed-channel configuration helpers"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    sample_rate_parser = subparsers.add_parser(
        "sample-rate",
        help="print the [rf] sample rate from an srsUE configuration",
    )
    sample_rate_parser.add_argument("config")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "sample-rate":
        print(format(sample_rate_from_ue_config(args.config), ".12g"))


if __name__ == "__main__":
    main()
