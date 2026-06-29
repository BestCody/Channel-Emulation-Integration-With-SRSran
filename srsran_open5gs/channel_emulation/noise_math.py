#!/usr/bin/env python3

import math


def finite_number(value, name):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


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


def sigma_for_snr(signal_power, snr_db):
    # AWGN sigma maps directly from target SNR
    signal_power = finite_number(signal_power, "signal_power")
    snr_db = finite_number(snr_db, "snr_db")
    if signal_power <= 0.0:
        raise ValueError("signal_power must be positive")
    target_noise_power = signal_power * 10.0 ** (-snr_db / 10.0)
    return {
        "signal_power": signal_power,
        "target_snr_db": snr_db,
        "target_noise_power": target_noise_power,
        "noise_sigma": math.sqrt(target_noise_power),
    }
