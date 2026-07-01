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
from channel_protocol import build_update  # noqa: E402
from channel_protocol import identity_taps  # noqa: E402
from noise_math import sigma_for_snr  # noqa: E402


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def stream_noise(client, direction, sigma):
    status = client.get_status()
    sequence = int(status["last_accepted_sequence"]) + 1
    message = build_update(
        identity_taps(),
        sequence,
        direction,
        noise_sigma=sigma,
        client_send_ns=time.time_ns(),
    )
    client.stream(message)
    return {"direction": direction, "sequence": sequence, "noise_sigma": sigma}


def collect_signal(client, duration, interval):
    deadline = time.monotonic() + duration
    samples = []
    while time.monotonic() < deadline:
        status = client.get_status()
        samples.append(
            {
                "time_ns": time.time_ns(),
                "downlink_signal": status["signal"]["downlink"],
                "uplink_signal": status["signal"]["uplink"],
            }
        )
        time.sleep(interval)
    return samples


def median_signal(samples, direction):
    values = [
        float(item[f"{direction}_signal"])
        for item in samples
        if item[f"{direction}_signal"] is not None
        and math.isfinite(float(item[f"{direction}_signal"]))
        and float(item[f"{direction}_signal"]) > 0.0
    ]
    if not values:
        raise ValueError(f"no positive {direction} signal samples")
    return statistics.median(values)


def signal_calibration(client, duration, interval):
    # Measure signal power with noise off
    stream_noise(client, "both", 0.0)
    time.sleep(0.2)
    samples = collect_signal(client, duration, interval)
    return {
        "schema_version": 1,
        "created_ns": time.time_ns(),
        "duration_seconds": duration,
        "interval_seconds": interval,
        "downlink_signal_power": median_signal(samples, "downlink"),
        "uplink_signal_power": median_signal(samples, "uplink"),
        "raw_samples": samples,
    }


def build_plan(signal_report, levels):
    downlink_power = signal_report["downlink_signal_power"]
    uplink_power = signal_report["uplink_signal_power"]
    result = {
        "schema_version": 1,
        "created_ns": time.time_ns(),
        "signal_calibration": {
            "downlink": downlink_power,
            "uplink": uplink_power,
        },
        "levels": [],
    }
    for level in levels:
        result["levels"].append(
            {
                "target_snr_db": float(level),
                "downlink": sigma_for_snr(downlink_power, level),
                "uplink": sigma_for_snr(uplink_power, level),
            }
        )
    return result


def level_from_plan(plan, target):
    for level in plan["levels"]:
        if float(level["target_snr_db"]) == float(target):
            return level
    raise ValueError(f"SNR level {target} is not in the frozen plan")


def apply_level(client, level):
    return {
        "downlink": stream_noise(
            client, "downlink", level["downlink"]["noise_sigma"]
        ),
        "uplink": stream_noise(
            client, "uplink", level["uplink"]["noise_sigma"]
        ),
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
    result.add_argument(
        "--stream-endpoint",
        default=os.environ.get("CHANNEL_STREAM_ENDPOINT", "tcp://127.0.0.1:5556"),
    )
    commands = result.add_subparsers(dest="command", required=True)

    calibrate = commands.add_parser("calibrate")
    calibrate.add_argument("--duration", type=float, default=5.0)
    calibrate.add_argument("--interval", type=float, default=0.05)
    calibrate.add_argument("--output", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--signal-calibration", required=True)
    plan.add_argument(
        "--levels",
        type=parse_levels,
        default=parse_levels("30,25,20,15,10,5,0"),
    )
    plan.add_argument("--output", required=True)

    apply_command = commands.add_parser("apply")
    apply_command.add_argument("--plan", required=True)
    apply_command.add_argument("--snr-db", type=float, required=True)
    apply_command.add_argument("--output", required=True)

    off = commands.add_parser("off")
    off.add_argument("--output", required=True)

    status = commands.add_parser("status")
    status.add_argument("--output", required=True)
    return result


def main():
    args = parser().parse_args()
    if args.command == "plan":
        signal_report = json.loads(
            pathlib.Path(args.signal_calibration).read_text(encoding="utf-8")
        )
        result = build_plan(signal_report, args.levels)
        write_json(args.output, result)
        print(json.dumps(result, sort_keys=True), flush=True)
        return

    client = ChannelClient(args.endpoint, stream_endpoint=args.stream_endpoint)
    try:
        if args.command == "calibrate":
            result = signal_calibration(client, args.duration, args.interval)
        elif args.command == "apply":
            plan = json.loads(
                pathlib.Path(args.plan).read_text(encoding="utf-8")
            )
            level = level_from_plan(plan, args.snr_db)
            result = {
                "target_snr_db": args.snr_db,
                "frozen_level": level,
                "control": apply_level(client, level),
            }
        elif args.command == "off":
            result = stream_noise(client, "both", 0.0)
        else:
            result = client.get_status()
        write_json(args.output, result)
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
