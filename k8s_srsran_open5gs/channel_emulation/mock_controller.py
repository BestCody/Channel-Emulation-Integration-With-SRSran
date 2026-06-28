#!/usr/bin/env python3

import argparse
import json
import pathlib
import sys
import time


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
sys.path.insert(0, str(LIVE_CONFIG))

from channel_protocol import attenuation_taps  # noqa: E402
from channel_protocol import build_update  # noqa: E402
from channel_protocol import identity_taps  # noqa: E402
from channel_protocol import safe_multipath_taps  # noqa: E402
from channel_client import ChannelClient  # noqa: E402


PROFILES = (
    ("identity", identity_taps),
    ("attenuation", attenuation_taps),
    ("safe_multipath", safe_multipath_taps),
    ("identity", identity_taps),
)


def append_json_line(path, record):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def wait_for_activation(client, sequence, timeout_seconds):
    started = time.perf_counter_ns()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = client.get_status()
        if (
            status["downlink"]["active_sequence"] == sequence
            and status["uplink"]["active_sequence"] == sequence
        ):
            status["activation_wait_ms"] = (
                time.perf_counter_ns() - started
            ) / 1_000_000.0
            return status
        time.sleep(0.02)
    raise TimeoutError(f"sequence {sequence} did not activate")


def invalid_messages(sequence, activate_at_sample):
    normal_tap = [{"delay": 0, "real": 1.0, "imag": 0.0}]
    return (
        (
            "stale_sequence",
            {
                "version": 1,
                "msg_type": "channel_update",
                "sequence": sequence - 1,
                "direction": "both",
                "activate_at_sample": activate_at_sample,
                "taps": normal_tap,
                "client_send_ns": time.time_ns(),
            },
        ),
        (
            "delay_256",
            {
                "version": 1,
                "msg_type": "channel_update",
                "sequence": sequence,
                "direction": "both",
                "activate_at_sample": activate_at_sample,
                "taps": [
                    {"delay": 256, "real": 1.0, "imag": 0.0}
                ],
                "client_send_ns": time.time_ns(),
            },
        ),
        (
            "forty_nine_taps",
            {
                "version": 1,
                "msg_type": "channel_update",
                "sequence": sequence,
                "direction": "both",
                "activate_at_sample": activate_at_sample,
                "taps": [
                    {
                        "delay": index,
                        "real": 0.01,
                        "imag": 0.0,
                    }
                    for index in range(49)
                ],
                "client_send_ns": time.time_ns(),
            },
        ),
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stage 4 mock live-channel controller"
    )
    parser.add_argument(
        "--endpoint",
        default="tcp://127.0.0.1:5555",
    )
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--activation-lead-ms", type=float, default=100.0)
    parser.add_argument("--activation-timeout", type=float, default=5.0)
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument(
        "--metrics",
        default="/tmp/stage4-mock-controller.jsonl",
    )
    parser.add_argument(
        "--invalid-metrics",
        default="/tmp/stage4-invalid-updates.jsonl",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.cycles < 1:
        raise ValueError("--cycles must be positive")

    client = ChannelClient(args.endpoint, args.timeout_ms)
    try:
        config = client.get_config()
        status = client.get_status()
        sequence = int(status["last_accepted_sequence"]) + 1
        lead_samples = max(
            1,
            int(config["sample_rate"] * args.activation_lead_ms / 1000.0),
        )
        print(f"config={config}", flush=True)

        for cycle in range(args.cycles):
            for profile_name, profile_factory in PROFILES:
                before = client.get_status()
                current_sample = max(
                    before["downlink"]["sample_count"],
                    before["uplink"]["sample_count"],
                )
                activate_at = current_sample + lead_samples
                client_send_ns = time.time_ns()
                message = build_update(
                    taps=profile_factory(),
                    sequence=sequence,
                    activate_at_sample=activate_at,
                    direction="both",
                    client_send_ns=client_send_ns,
                )
                ack = client.request(message)
                active = wait_for_activation(
                    client,
                    sequence,
                    args.activation_timeout,
                )
                record = {
                    "cycle": cycle,
                    "profile": profile_name,
                    "sequence": sequence,
                    "tap_count": len(message["taps"]),
                    "requested_activation_sample": activate_at,
                    "ack": ack,
                    "downlink": active["downlink"],
                    "uplink": active["uplink"],
                    "activation_wait_ms": active["activation_wait_ms"],
                    "downlink_activation_error_samples": (
                        active["downlink"]["actual_activation_sample"]
                        - activate_at
                    ),
                    "uplink_activation_error_samples": (
                        active["uplink"]["actual_activation_sample"]
                        - activate_at
                    ),
                }
                append_json_line(args.metrics, record)
                print(
                    f"cycle={cycle} profile={profile_name} "
                    f"sequence={sequence} "
                    f"rtt_ms={ack['request_rtt_ms']:.3f} "
                    f"schedule_us={ack['schedule_us']:.3f} "
                    f"dl_error={record['downlink_activation_error_samples']} "
                    f"ul_error={record['uplink_activation_error_samples']}",
                    flush=True,
                )
                sequence += 1
                time.sleep(args.interval)

        final_status = client.get_status()
        invalid_activation = max(
            final_status["downlink"]["sample_count"],
            final_status["uplink"]["sample_count"],
        ) + lead_samples
        active_before_invalid = (
            final_status["downlink"]["active_sequence"],
            final_status["uplink"]["active_sequence"],
        )
        for name, message in invalid_messages(
            sequence,
            invalid_activation,
        ):
            response = client.request(message, raise_on_error=False)
            after = client.get_status()
            record = {
                "name": name,
                "response": response,
                "active_before": active_before_invalid,
                "active_after": (
                    after["downlink"]["active_sequence"],
                    after["uplink"]["active_sequence"],
                ),
                "pending_after": (
                    after["downlink"]["pending_sequence"],
                    after["uplink"]["pending_sequence"],
                ),
            }
            append_json_line(args.invalid_metrics, record)
            if response.get("msg_type") != "error":
                raise RuntimeError(f"invalid update {name} was accepted")
            if record["active_after"] != active_before_invalid:
                raise RuntimeError(f"invalid update {name} changed channel")
            print(f"invalid={name} rejected={response['error']}", flush=True)

        print(f"final_status={client.get_status()}", flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
