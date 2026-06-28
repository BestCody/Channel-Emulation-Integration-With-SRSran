#!/usr/bin/env python3

import json
import math
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAXIMUM_NOISE_AMPLITUDE = 512.0


def finite_number(value, name):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class NoiseCalibration:
    scale: float
    exponent: float
    maximum_amplitude: float
    gnuradio_version: str
    image_id: str

    def power_from_amplitude(self, amplitude):
        amplitude = finite_number(amplitude, "amplitude")
        if amplitude < 0.0:
            raise ValueError("amplitude cannot be negative")
        if amplitude > self.maximum_amplitude:
            raise ValueError(
                f"amplitude {amplitude} exceeds maximum allowed "
                f"{self.maximum_amplitude}"
            )
        if amplitude == 0.0:
            return 0.0
        return self.scale * amplitude ** self.exponent

    def amplitude_from_power(self, power):
        power = finite_number(power, "power")
        if power < 0.0:
            raise ValueError("power cannot be negative")
        if power == 0.0:
            return 0.0
        amplitude = (power / self.scale) ** (1.0 / self.exponent)
        if amplitude > self.maximum_amplitude:
            raise ValueError(
                f"calculated amplitude {amplitude} exceeds maximum "
                f"allowed {self.maximum_amplitude}"
            )
        return amplitude

    def amplitude_for_snr(self, signal_power, snr_db):
        signal_power = finite_number(signal_power, "signal_power")
        snr_db = finite_number(snr_db, "snr_db")
        if signal_power <= 0.0:
            raise ValueError("signal_power must be positive")
        target_noise_power = signal_power * 10.0 ** (-snr_db / 10.0)
        amplitude = self.amplitude_from_power(target_noise_power)
        return {
            "signal_power": signal_power,
            "target_snr_db": snr_db,
            "target_noise_power": target_noise_power,
            "amplitude": amplitude,
        }


def load_noise_calibration(
    path,
    expected_gnuradio_version="3.8.1.0",
    expected_image_id=None,
    maximum_allowed_amplitude=DEFAULT_MAXIMUM_NOISE_AMPLITUDE,
):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported noise calibration schema")
    if data.get("source_block") != "analog.noise_source_c":
        raise ValueError("unexpected calibrated noise source")
    if data.get("noise_type") != "GR_GAUSSIAN":
        raise ValueError("unexpected calibrated noise type")
    if data.get("gnuradio_version") != expected_gnuradio_version:
        raise ValueError("GNU Radio version does not match calibration")
    if expected_image_id and data.get("image_id") != expected_image_id:
        raise ValueError("container image does not match calibration")

    fit = data.get("fit", {})
    scale = finite_number(fit.get("scale"), "fit scale")
    exponent = finite_number(fit.get("exponent"), "fit exponent")
    r_squared = finite_number(fit.get("r_squared"), "fit r_squared")
    relative_error = finite_number(
        fit.get("maximum_relative_error"),
        "fit maximum_relative_error",
    )
    calibrated_max = finite_number(
        data.get("maximum_calibrated_amplitude"),
        "maximum_calibrated_amplitude",
    )
    policy_max = finite_number(
        maximum_allowed_amplitude,
        "maximum_allowed_amplitude",
    )
    if scale <= 0.0 or exponent <= 0.0:
        raise ValueError("noise calibration fit must be positive")
    if r_squared < 0.9999:
        raise ValueError("noise calibration fit is not stable")
    if relative_error > 0.01:
        raise ValueError("noise calibration fit error exceeds 1%")
    if calibrated_max <= 0.0 or policy_max <= 0.0:
        raise ValueError("maximum amplitude must be positive")

    return NoiseCalibration(
        scale=scale,
        exponent=exponent,
        maximum_amplitude=min(calibrated_max, policy_max),
        gnuradio_version=data["gnuradio_version"],
        image_id=data.get("image_id", ""),
    )


def measured_snr_db(signal_power, noise_power):
    signal_power = finite_number(signal_power, "signal_power")
    noise_power = finite_number(noise_power, "noise_power")
    if signal_power <= 0.0:
        raise ValueError("signal_power must be positive")
    if noise_power < 0.0:
        raise ValueError("noise_power cannot be negative")
    if noise_power == 0.0:
        return math.inf
    return 10.0 * math.log10(signal_power / noise_power)
