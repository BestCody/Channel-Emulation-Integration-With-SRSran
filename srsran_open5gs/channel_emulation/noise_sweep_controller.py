#!/usr/bin/env python3

import argparse
import os
import json
import math
import pathlib
import statistics
import sys
import time


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
NOISE_CONFIG = REPO_ROOT / "configs" / "ues" / "srsue-noise" / "config"
sys.path.insert(0, str(LIVE_CONFIG))
sys.path.insert(0, str(NOISE_CONFIG))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from channel_client import ChannelClient  # noqa: E402
from noise_math import load_noise_calibration  # noqa: E402
from noise_math import measured_snr_db  # noqa: E402
from noise_protocol import build_noise_update  # noqa: E402


DEFAULT_CALIBRATION = (
    REPO_ROOT / "channel_emulation/noise_calibration_gr381.json"
)


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def finite_positive_samples(values, name):
    selected = [
        float(value)
        for value in values
        if value is not None
        and math.isfinite(float(value))
        and float(value) > 0.0
    ]
    if not selected:
        raise ValueError(f"no positive finite {name} samples")
    return selected


def collect_status(client, duration, interval):
    deadline = time.monotonic() + duration
    samples = []
    while time.monotonic() < deadline:
        status = client.get_status()
        samples.append(
            {
                "time_ns": time.time_ns(),
                "downlink_signal":
                    status["noise"]["downlink"]["signal_power"],
                "uplink_signal":
                    status["noise"]["uplink"]["signal_power"],
                "downlink_noise":
                    status["noise"]["downlink"]["noise_power"],
                "uplink_noise":
                    status["noise"]["uplink"]["noise_power"],
                "downlink_amplitude":
                    status["noise"]["downlink"]["amplitude"],
                "uplink_amplitude":
                    status["noise"]["uplink"]["amplitude"],
            }
        )
        time.sleep(interval)
    return samples


def summarize_direction(samples, direction):
    signal = finite_positive_samples(
        [item[f"{direction}_signal"] for item in samples],
        f"{direction} signal power",
    )
    noise = [
        float(item[f"{direction}_noise"])
        for item in samples
        if item[f"{direction}_noise"] is not None
        and math.isfinite(float(item[f"{direction}_noise"]))
        and float(item[f"{direction}_noise"]) >= 0.0
    ]
    signal_power = statistics.median(signal)
    noise_power = statistics.median(noise) if noise else None
    snr = None
    if noise_power is not None and noise_power > 0.0:
        snr = measured_snr_db(signal_power, noise_power)
    return {
        "signal_power": signal_power,
        "noise_power": noise_power,
        "measured_snr_db": snr,
        "signal_samples": len(signal),
        "noise_samples": len(noise),
    }


def signal_calibration(client, duration, interval):
    before = client.get_status()
    noise = before["noise"]
    if (
        noise["downlink"]["amplitude"] != 0.0
        or noise["uplink"]["amplitude"] != 0.0
    ):
        raise ValueError("signal calibration requires noise amplitude zero")
    samples = collect_status(client, duration, interval)
    return {
        "schema_version": 1,
        "created_ns": time.time_ns(),
        "duration_seconds": duration,
        "interval_seconds": interval,
        "continuous_adjustment": False,
        "downlink": summarize_direction(samples, "downlink"),
        "uplink": summarize_direction(samples, "uplink"),
        "raw_samples": samples,
    }


def build_plan(signal_report, calibration, levels):
    result = {
        "schema_version": 1,
        "created_ns": time.time_ns(),
        "maximum_amplitude": calibration.maximum_amplitude,
        "formula": {
            "scale": calibration.scale,
            "exponent": calibration.exponent,
        },
        "signal_calibration": {
            "downlink":
                signal_report["downlink"]["signal_power"],
            "uplink":
                signal_report["uplink"]["signal_power"],
        },
        "levels": [],
    }
    for level in levels:
        downlink = calibration.amplitude_for_snr(
            result["signal_calibration"]["downlink"],
            level,
        )
        uplink = calibration.amplitude_for_snr(
            result["signal_calibration"]["uplink"],
            level,
        )
        result["levels"].append(
            {
                "target_snr_db": float(level),
                "downlink": downlink,
                "uplink": uplink,
            }
        )
    return result


def level_from_plan(plan, target):
    for level in plan["levels"]:
        if float(level["target_snr_db"]) == float(target):
            return level
    raise ValueError(f"SNR level {target} is not in the frozen plan")


def apply_amplitudes(client, amplitudes):
    status = client.get_status()
    sequence = int(
        status["noise"]["last_accepted_noise_sequence"]
    ) + 1
    message = build_noise_update(
        sequence=sequence,
        direction="both",
        amplitudes=amplitudes,
        client_send_ns=time.time_ns(),
    )
    ack = client.request(message)
    after = client.get_status()
    return {
        "message": message,
        "ack": ack,
        "status": after["noise"],
    }


def parse_levels(value):
    levels = [float(item) for item in value.split(",")]
    if not levels or any(not math.isfinite(item) for item in levels):
        raise argparse.ArgumentTypeError("levels must be finite")
    return levels


def parser():
    result = argparse.ArgumentParser()
    result.add_argument(
        "--endpoint",
        default=os.environ.get("CHANNEL_CONTROL_ENDPOINT", "tcp://127.0.0.1:5555"),
    )
    commands = result.add_subparsers(dest="command", required=True)

    calibrate = commands.add_parser("calibrate")
    calibrate.add_argument("--duration", type=float, default=5.0)
    calibrate.add_argument("--interval", type=float, default=0.05)
    calibrate.add_argument("--output", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--signal-calibration", required=True)
    plan.add_argument(
        "--noise-calibration",
        default=str(DEFAULT_CALIBRATION),
    )
    plan.add_argument(
        "--levels",
        type=parse_levels,
        default=parse_levels("30,25,20,15,10,5,0"),
    )
    plan.add_argument("--output", required=True)

    apply_level = commands.add_parser("apply")
    apply_level.add_argument("--plan", required=True)
    apply_level.add_argument("--snr-db", type=float, required=True)
    apply_level.add_argument("--output", required=True)

    off = commands.add_parser("off")
    off.add_argument("--output", required=True)

    measure = commands.add_parser("measure")
    measure.add_argument("--duration", type=float, default=2.0)
    measure.add_argument("--interval", type=float, default=0.05)
    measure.add_argument("--output", required=True)

    status = commands.add_parser("status")
    status.add_argument("--output", required=True)
    return result


def main():
    args = parser().parse_args()
    if args.command == "plan":
        calibration = load_noise_calibration(args.noise_calibration)
        signal_report = json.loads(
            pathlib.Path(args.signal_calibration).read_text(
                encoding="utf-8"
            )
        )
        result = build_plan(signal_report, calibration, args.levels)
        write_json(args.output, result)
        print(json.dumps(result, sort_keys=True), flush=True)
        return

    client = ChannelClient(args.endpoint)
    try:
        if args.command == "calibrate":
            result = signal_calibration(
                client,
                args.duration,
                args.interval,
            )
        elif args.command == "apply":
            plan = json.loads(
                pathlib.Path(args.plan).read_text(encoding="utf-8")
            )
            level = level_from_plan(plan, args.snr_db)
            result = {
                "target_snr_db": args.snr_db,
                "frozen_level": level,
                "control": apply_amplitudes(
                    client,
                    {
                        "downlink":
                            level["downlink"]["amplitude"],
                        "uplink":
                            level["uplink"]["amplitude"],
                    },
                ),
            }
        elif args.command == "off":
            result = apply_amplitudes(
                client,
                {"downlink": 0.0, "uplink": 0.0},
            )
        elif args.command == "measure":
            samples = collect_status(
                client,
                args.duration,
                args.interval,
            )
            result = {
                "created_ns": time.time_ns(),
                "downlink": summarize_direction(samples, "downlink"),
                "uplink": summarize_direction(samples, "uplink"),
                "raw_samples": samples,
            }
        else:
            result = client.get_status()
        write_json(args.output, result)
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
