#!/usr/bin/env python3

import argparse
import os
import json
import pathlib
import sys
import time


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
sys.path.insert(0, str(LIVE_CONFIG))

from channel_protocol import MAX_DELAY  # noqa: E402
from channel_protocol import MAX_TAPS  # noqa: E402
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


def invalid_messages(sequence):
    return (
        (
            "delay_above_max",
            {
                "version": 1,
                "msg_type": "channel_update",
                "sequence": sequence,
                "direction": "both",
                "taps": [
                    {"delay": MAX_DELAY + 1, "real": 1.0, "imag": 0.0}
                ],
                "client_send_ns": time.time_ns(),
            },
        ),
        (
            "too_many_taps",
            {
                "version": 1,
                "msg_type": "channel_update",
                "sequence": sequence,
                "direction": "both",
                "taps": [
                    {"delay": index, "real": 0.01, "imag": 0.0}
                    for index in range(MAX_TAPS + 1)
                ],
                "client_send_ns": time.time_ns(),
            },
        ),
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Mock live-channel controller"
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("CHANNEL_CONTROL_ENDPOINT", "tcp://127.0.0.1:5555"),
    )
    parser.add_argument(
        "--stream-endpoint",
        default=os.environ.get("CHANNEL_STREAM_ENDPOINT", "tcp://127.0.0.1:5556"),
    )
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument(
        "--metrics",
        default=os.environ.get("MOCK_CONTROLLER_LOG", "/tmp/live-channel-mock-controller.jsonl"),
    )
    parser.add_argument(
        "--invalid-metrics",
        default=os.environ.get("MOCK_CONTROLLER_INVALID_LOG", "/tmp/live-channel-invalid-updates.jsonl"),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.cycles < 1:
        raise ValueError("--cycles must be positive")

    client = ChannelClient(
        args.endpoint,
        args.timeout_ms,
        stream_endpoint=args.stream_endpoint,
    )
    try:
        config = client.get_config()
        status = client.get_status()
        sequence = int(status["last_accepted_sequence"]) + 1
        print(f"config={config}", flush=True)

        for cycle in range(args.cycles):
            for profile_name, profile_factory in PROFILES:
                message = build_update(
                    taps=profile_factory(),
                    sequence=sequence,
                    direction="both",
                    client_send_ns=time.time_ns(),
                )
                client.stream(message)
                time.sleep(0.05)
                after = client.get_status()
                record = {
                    "cycle": cycle,
                    "profile": profile_name,
                    "sequence": sequence,
                    "tap_count": len(message["taps"]),
                    "downlink": after["downlink"],
                    "uplink": after["uplink"],
                }
                append_json_line(args.metrics, record)
                print(
                    f"cycle={cycle} profile={profile_name} "
                    f"sequence={sequence} "
                    f"dl_updates={after['downlink']['update_count']} "
                    f"ul_updates={after['uplink']['update_count']}",
                    flush=True,
                )
                sequence += 1
                time.sleep(args.interval)

        before_invalid = client.get_status()
        updates_before = (
            before_invalid["downlink"]["update_count"],
            before_invalid["uplink"]["update_count"],
        )
        for name, message in invalid_messages(sequence):
            client.stream(message)
            time.sleep(0.1)
            after = client.get_status()
            updates_after = (
                after["downlink"]["update_count"],
                after["uplink"]["update_count"],
            )
            record = {
                "name": name,
                "updates_before": updates_before,
                "updates_after": updates_after,
            }
            append_json_line(args.invalid_metrics, record)
            if updates_after != updates_before:
                raise RuntimeError(f"invalid update {name} changed channel")
            print(f"invalid={name} dropped", flush=True)

        print(f"final_status={client.get_status()}", flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
