#!/usr/bin/env python3

import argparse
import copy
import os
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
    sample_ue_positions,
    scene_bounding_box,
)
from sionna_taps import taps_from_report  # noqa: E402


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def print_report(report):
    conversion = report["conversion"]
    print("Original Sionna paths:", flush=True)
    for path in conversion["original_paths"]:
        coefficient = path["coefficient"]
        sample_delay = path.get("sample_delay")
        rounded = "-" if sample_delay is None else round(sample_delay)
        print(
            "path={index} status={status} delay_s={delay_seconds:.12g} "
            "sample_delay={rounded} "
            "coefficient={real:+.12g}{imag:+.12g}j "
            "power={power:.12g}".format(
                real=coefficient["real"],
                imag=coefficient["imag"],
                rounded=rounded,
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


def conversion_protocol_taps(conversion):
    errors = []
    if not conversion.get("safe_to_send"):
        errors.append("solved channel is not safe to send")
    if conversion.get("errors"):
        errors.append("solved channel contains validation errors")
    if conversion.get("normalization") != "none":
        errors.append("initial runtime requires normalization=none")
    if not conversion.get("absolute_coefficients_preserved"):
        errors.append("absolute coefficients were not preserved")
    if not conversion.get("retained_taps"):
        errors.append("solved channel has no retained taps")
    if errors:
        raise ValueError("; ".join(errors))

    protocol_taps = tuple(
        ProtocolTap(tap.delay, tap.coefficient)
        for tap in taps_from_report(conversion)
    )
    validate_taps(protocol_taps)
    return protocol_taps


def validate_radio_match(report, radio):
    errors = []
    if float(report.get("carrier_hz", -1)) != radio.carrier_hz:
        errors.append("solved carrier does not match gNB configuration")
    if float(report.get("sample_rate", -1)) != radio.sample_rate:
        errors.append("solved sample rate does not match UE configuration")
    if errors:
        raise ValueError("; ".join(errors))


def build_ue_configs(args, num_ues):
    # one scene config per UE, sharing a single transmitter
    if num_ues > 1:
        if args.placement_mode != "random":
            raise ValueError(
                "multi-UE (--num-ues > 1) requires --placement-mode random"
            )
        base = load_scene_config(args.scene_config, placement_mode="configured")
        bounds = scene_bounding_box(base["scene"])
        min_distance = (
            args.placement_min_distance
            if args.placement_min_distance is not None
            else base.get("placement", {}).get("min_distance_m", 0.0)
        )
        transmitter, receivers = sample_ue_positions(
            bounds,
            num_ues,
            seed=args.placement_seed,
            min_distance=min_distance,
        )
        configs = []
        for receiver in receivers:
            config = copy.deepcopy(base)
            config["transmitter"]["position"] = list(transmitter)
            config["receiver"]["position"] = list(receiver)
            config["resolved_placement"] = {
                "mode": "random",
                "seed": args.placement_seed,
                "scene_bounds": {"min": bounds[0], "max": bounds[1]},
                "transmitter": list(transmitter),
                "receiver": list(receiver),
                "min_distance_m": float(min_distance),
            }
            configs.append(config)
        return list(transmitter), configs
    config = load_scene_config(
        args.scene_config,
        placement_mode=args.placement_mode,
        placement_seed=args.placement_seed,
        min_distance_m=args.placement_min_distance,
    )
    return list(config["transmitter"]["position"]), [config]


def solve_channel(args, radio, num_ues):
    transmitter, configs = build_ue_configs(args, num_ues)
    ues = []
    for index, config in enumerate(configs):
        report = calculate_stationary_channel(
            config,
            carrier_hz=radio.carrier_hz,
            sample_rate=radio.sample_rate,
            repeats=args.repeats,
        )
        report["ue_index"] = index + 1
        ues.append(report)
    return {
        "schema_version": 1,
        "num_ues": num_ues,
        "carrier_hz": radio.carrier_hz,
        "sample_rate": radio.sample_rate,
        "radio_config": {"nr_arfcn": radio.nr_arfcn, "band": radio.band},
        "transmitter": {"position": list(transmitter)},
        "ues": ues,
    }


def stream_channel(args, radio, solved):
    validate_radio_match(solved, radio)
    num_ues = int(solved["num_ues"])
    per_ue = [
        (int(ue["ue_index"]), conversion_protocol_taps(ue.get("conversion", {})))
        for ue in solved["ues"]
    ]

    client = ChannelClient(args.endpoint, stream_endpoint=args.stream_endpoint)
    try:
        config = client.get_config()
        if float(config["sample_rate"]) != radio.sample_rate:
            raise ValueError(
                "live channel sample rate does not match solved channel"
            )
        if int(config.get("num_ues", 1)) != num_ues:
            raise ValueError(
                f"live flowgraph has {config.get('num_ues')} UEs, "
                f"solved {num_ues}"
            )
        sequence = int(client.get_status()["last_accepted_sequence"]) + 1
        streamed = []
        for ue_index, taps in per_ue:
            message = build_update(
                taps=taps,
                sequence=sequence,
                direction="both",
                client_send_ns=time.time_ns(),
                ue_index=ue_index,
            )
            client.stream(message)
            streamed.append({
                "ue_index": ue_index,
                "sequence": sequence,
                "tap_count": len(taps),
            })
            sequence += 1
        after = client.get_status()
        return {
            "streamed": streamed,
            "downlink": after["downlink"],
            "uplink": after["uplink"],
        }
    finally:
        client.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stationary Sionna RT channel controller"
    )
    parser.add_argument(
        "--scene-config",
        default=str(
            REPO_ROOT
            / "channel_emulation/scenes/default_scene.json"
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
    parser.add_argument("--placement-min-distance", type=float)
    parser.add_argument("--num-ues", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--endpoint", default=os.environ.get("CHANNEL_CONTROL_ENDPOINT", "tcp://127.0.0.1:5555"))
    parser.add_argument("--stream-endpoint", default=os.environ.get("CHANNEL_STREAM_ENDPOINT", "tcp://127.0.0.1:5556"))
    return parser.parse_args()


def main():
    args = parse_args()
    radio = load_radio_config(args.gnb_config, args.ue_config)
    num_ues = int(args.num_ues)
    if num_ues < 1:
        raise ValueError("--num-ues must be at least one")

    solved = solve_channel(args, radio, num_ues)
    for ue in solved["ues"]:
        print(f"--- UE {ue['ue_index']} ---", flush=True)
        print_report(ue)
    if not all(ue["conversion"]["safe_to_send"] for ue in solved["ues"]):
        raise SystemExit(2)

    stream = stream_channel(args, radio, solved)
    result = dict(solved)
    result.update(stream)
    write_json(args.output, result)
    print(
        json.dumps(
            {
                "output": args.output,
                "num_ues": num_ues,
                "streamed": stream["streamed"],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
