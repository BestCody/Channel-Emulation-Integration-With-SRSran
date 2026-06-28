#!/usr/bin/env python3

import argparse
import json
import math
from pathlib import Path

import numpy
from gnuradio import analog
from gnuradio import blocks
from gnuradio import gr


def parse_amplitudes(value):
    amplitudes = [float(item) for item in value.split(",")]
    if not amplitudes or any(
        not math.isfinite(item) or item <= 0.0 for item in amplitudes
    ):
        raise argparse.ArgumentTypeError(
            "amplitudes must be finite positive values"
        )
    return amplitudes


def measure_noise(amplitude, samples, discard, seed):
    top = gr.top_block()
    source = analog.noise_source_c(
        analog.GR_GAUSSIAN,
        amplitude,
        seed,
    )
    head = blocks.head(gr.sizeof_gr_complex, samples + discard)
    sink = blocks.vector_sink_c()
    top.connect(source, head, sink)
    top.run()

    values = numpy.asarray(sink.data(), dtype=numpy.complex64)[discard:]
    real = values.real.astype(numpy.float64)
    imag = values.imag.astype(numpy.float64)
    real_mean = float(numpy.mean(real))
    imag_mean = float(numpy.mean(imag))
    real_variance = float(numpy.var(real))
    imag_variance = float(numpy.var(imag))
    complex_power = float(numpy.mean(numpy.abs(values) ** 2))
    centered_power = real_variance + imag_variance

    return {
        "amplitude": amplitude,
        "seed": seed,
        "sample_count": int(values.size),
        "real_mean": real_mean,
        "imag_mean": imag_mean,
        "real_variance": real_variance,
        "imag_variance": imag_variance,
        "centered_complex_power": centered_power,
        "total_complex_power": complex_power,
    }


def fit_power_law(measurements):
    amplitudes = numpy.asarray(
        [item["amplitude"] for item in measurements],
        dtype=numpy.float64,
    )
    powers = numpy.asarray(
        [item["total_complex_power"] for item in measurements],
        dtype=numpy.float64,
    )
    log_amplitudes = numpy.log(amplitudes)
    log_powers = numpy.log(powers)
    exponent, log_scale = numpy.polyfit(log_amplitudes, log_powers, 1)
    predicted_logs = exponent * log_amplitudes + log_scale
    residual = float(numpy.sum((log_powers - predicted_logs) ** 2))
    centered = float(
        numpy.sum((log_powers - numpy.mean(log_powers)) ** 2)
    )
    r_squared = 1.0 if centered == 0.0 else 1.0 - residual / centered
    scale = float(math.exp(log_scale))
    predicted = scale * numpy.power(amplitudes, exponent)
    maximum_relative_error = float(
        numpy.max(numpy.abs(predicted - powers) / powers)
    )
    return {
        "scale": scale,
        "exponent": float(exponent),
        "r_squared": r_squared,
        "maximum_relative_error": maximum_relative_error,
        "formula": "total_complex_power = scale * amplitude ** exponent",
    }


def validate_measurements(measurements, fit):
    errors = []
    for item in measurements:
        amplitude = item["amplitude"]
        largest_mean = max(
            abs(item["real_mean"]),
            abs(item["imag_mean"]),
        )
        if largest_mean > amplitude * 0.005:
            errors.append(
                f"amplitude {amplitude}: component mean is too large"
            )
        variance_sum = (
            item["real_variance"] + item["imag_variance"]
        )
        variance_difference = abs(
            item["real_variance"] - item["imag_variance"]
        )
        if variance_sum == 0.0 or variance_difference / variance_sum > 0.01:
            errors.append(
                f"amplitude {amplitude}: I/Q variances are unbalanced"
            )
        mean_power = (
            item["real_mean"] ** 2 + item["imag_mean"] ** 2
        )
        if abs(
            item["total_complex_power"]
            - item["centered_complex_power"]
            - mean_power
        ) > max(item["total_complex_power"] * 1e-5, 1e-12):
            errors.append(
                f"amplitude {amplitude}: power statistics disagree"
            )

    if fit["r_squared"] < 0.9999:
        errors.append("amplitude-to-power fit is not stable")
    if fit["maximum_relative_error"] > 0.01:
        errors.append("power-law fit exceeds 1% relative error")
    if errors:
        raise RuntimeError("; ".join(errors))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=2_000_000)
    parser.add_argument("--discard", type=int, default=4_096)
    parser.add_argument(
        "--amplitudes",
        type=parse_amplitudes,
        default=parse_amplitudes("0.05,0.1,0.25,0.5,1.0"),
    )
    parser.add_argument("--seed", type=int, default=-20260621)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.samples < 100_000:
        parser.error("--samples must be at least 100000")
    if args.discard < 0:
        parser.error("--discard cannot be negative")

    measurements = [
        measure_noise(
            amplitude,
            args.samples,
            args.discard,
            args.seed - index,
        )
        for index, amplitude in enumerate(args.amplitudes)
    ]
    fit = fit_power_law(measurements)
    validate_measurements(measurements, fit)
    report = {
        "schema_version": 1,
        "gnuradio_version": gr.version(),
        "source_block": "analog.noise_source_c",
        "noise_type": "GR_GAUSSIAN",
        "discarded_samples": args.discard,
        "measurements": measurements,
        "fit": fit,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
