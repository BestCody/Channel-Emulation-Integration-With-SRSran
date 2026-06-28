#!/usr/bin/env python3

import argparse
import os
import hashlib
import json
import pathlib
import sys
import time


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
sys.path.insert(0, str(LIVE_CONFIG))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from channel_client import ChannelClient  # noqa: E402
from channel_protocol import Tap as ProtocolTap  # noqa: E402
from channel_protocol import build_update  # noqa: E402
from channel_protocol import validate_taps  # noqa: E402
from sionna_radio_config import load_radio_config  # noqa: E402
from sionna_stationary import (  # noqa: E402
    calculate_stationary_channel,
    load_scene_config,
)
from sionna_taps import taps_from_report  # noqa: E402


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def report_sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def print_report(report):
    conversion = report["conversion"]
    print("Original Sionna paths:", flush=True)
    for path in conversion["original_paths"]:
        coefficient = path["coefficient"]
        print(
            "path={index} status={status} delay_s={delay_seconds:.12g} "
            "sample_delay={rounded_sample_delay} "
            "coefficient={real:+.12g}{imag:+.12g}j "
            "power={power:.12g}".format(
                real=coefficient["real"],
                imag=coefficient["imag"],
                **path,
            ),
            flush=True,
        )
    print("Combined taps:", flush=True)
    for tap in conversion["combined_taps"]:
        print(
            "delay={delay} coefficient={real:+.12g}{imag:+.12g}j "
            "power={power:.12g}".format(**tap),
            flush=True,
        )
    print(
        "retained_power={:.12g} discarded_power={:.12g} "
        "normalization={} absolute_coefficients_preserved={} "
        "safe_to_send={}".format(
            conversion["retained_power"],
            conversion["discarded_power"],
            conversion["normalization"],
            conversion["absolute_coefficients_preserved"],
            conversion["safe_to_send"],
        ),
        flush=True,
    )
    if conversion["errors"]:
        print(
            "validation_errors=" + "; ".join(conversion["errors"]),
            flush=True,
        )


def validate_saved_report(report, radio):
    conversion = report.get("conversion", {})
    errors = []
    if not conversion.get("safe_to_send"):
        errors.append("dry-run report is not safe to send")
    if conversion.get("errors"):
        errors.append("dry-run report contains validation errors")
    if conversion.get("normalization") != "none":
        errors.append("initial runtime requires normalization=none")
    if not conversion.get("absolute_coefficients_preserved"):
        errors.append("absolute coefficients were not preserved")
    if not conversion.get("retained_taps"):
        errors.append("dry-run report has no retained taps")
    if float(report.get("carrier_hz", -1)) != radio.carrier_hz:
        errors.append("dry-run carrier does not match gNB configuration")
    if float(report.get("sample_rate", -1)) != radio.sample_rate:
        errors.append("dry-run sample rate does not match UE configuration")
    if errors:
        raise ValueError("; ".join(errors))

    protocol_taps = tuple(
        ProtocolTap(tap.delay, tap.coefficient)
        for tap in taps_from_report(conversion)
    )
    validate_taps(protocol_taps)
    return protocol_taps


def wait_for_activation(client, sequence, timeout):
    started = time.perf_counter_ns()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get_status()
        if (
            status["downlink"]["active_sequence"] == sequence
            and status["uplink"]["active_sequence"] == sequence
        ):
            return status, (
                time.perf_counter_ns() - started
            ) / 1_000_000
        time.sleep(0.02)
    raise TimeoutError(f"sequence {sequence} did not activate")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stationary Sionna RT Stage 5 controller"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--send-report")
    parser.add_argument(
        "--scene-config",
        default=str(
            REPO_ROOT
            / "channel_emulation/scenes/stationary_reflector.json"
        ),
    )
    parser.add_argument(
        "--gnb-config",
        default=str(
            REPO_ROOT
            / "configs/srsRAN/srsran-gnb/config/srsran-gnb.yaml"
        ),
    )
    parser.add_argument(
        "--ue-config",
        default=str(REPO_ROOT / "configs/ues/srsue/config/ue0.conf"),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--placement-mode", choices=["configured", "random"])
    parser.add_argument("--placement-seed", type=int)
    parser.add_argument("--placement-max-attempts", type=int)
    parser.add_argument("--placement-min-distance", type=float)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--endpoint", default=os.environ.get("CHANNEL_CONTROL_ENDPOINT", "tcp://127.0.0.1:5555"))
    parser.add_argument("--activation-lead-ms", type=float, default=100)
    parser.add_argument("--activation-timeout", type=float, default=8)
    parser.add_argument("--expected-report-sha256")
    return parser.parse_args()


def main():
    args = parse_args()
    radio = load_radio_config(args.gnb_config, args.ue_config)

    if args.dry_run:
        config = load_scene_config(
            args.scene_config,
            placement_mode=args.placement_mode,
            placement_seed=args.placement_seed,
            max_attempts=args.placement_max_attempts,
            min_distance_m=args.placement_min_distance,
        )
        report = calculate_stationary_channel(
            config,
            carrier_hz=radio.carrier_hz,
            sample_rate=radio.sample_rate,
            repeats=args.repeats,
        )
        report["radio_config"] = {
            "nr_arfcn": radio.nr_arfcn,
            "band": radio.band,
        }
        write_json(args.output, report)
        print_report(report)
        print(
            f"report={args.output} sha256={report_sha256(args.output)}",
            flush=True,
        )
        if not report["conversion"]["safe_to_send"]:
            raise SystemExit(2)
        return

    report_path = pathlib.Path(args.send_report)
    actual_sha256 = report_sha256(report_path)
    if (
        args.expected_report_sha256
        and actual_sha256 != args.expected_report_sha256
    ):
        raise ValueError("dry-run report checksum does not match")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    protocol_taps = validate_saved_report(report, radio)
    print_report(report)

    client = ChannelClient(args.endpoint)
    try:
        config = client.get_config()
        if float(config["sample_rate"]) != radio.sample_rate:
            raise ValueError(
                "live Stage 4 sample rate does not match dry-run"
            )
        before = client.get_status()
        sequence = int(before["last_accepted_sequence"]) + 1
        current_sample = max(
            before["downlink"]["sample_count"],
            before["uplink"]["sample_count"],
        )
        activate_at = current_sample + max(
            1,
            int(
                radio.sample_rate
                * args.activation_lead_ms
                / 1000.0
            ),
        )
        message = build_update(
            taps=protocol_taps,
            sequence=sequence,
            activate_at_sample=activate_at,
            direction="both",
            client_send_ns=time.time_ns(),
        )
        ack = client.request(message)
        active, activation_wait_ms = wait_for_activation(
            client,
            sequence,
            args.activation_timeout,
        )
        result = {
            "report": str(report_path),
            "report_sha256": actual_sha256,
            "sequence": sequence,
            "tap_count": len(protocol_taps),
            "requested_activation_sample": activate_at,
            "ack": ack,
            "activation_wait_ms": activation_wait_ms,
            "downlink": active["downlink"],
            "uplink": active["uplink"],
            "downlink_activation_error_samples":
                active["downlink"]["actual_activation_sample"] - activate_at,
            "uplink_activation_error_samples":
                active["uplink"]["actual_activation_sample"] - activate_at,
        }
        write_json(args.output, result)
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
