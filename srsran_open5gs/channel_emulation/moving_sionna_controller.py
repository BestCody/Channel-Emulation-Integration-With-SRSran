import argparse
import os
import hashlib
import json
import math
import pathlib
import sys
import time


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "configs/ues/srsue-live/config"
sys.path.insert(0, str(LIVE_CONFIG))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from channel_client import ChannelClient  # noqa: E402
from channel_protocol import Tap as ProtocolTap  # noqa: E402
from channel_protocol import build_update  # noqa: E402
from channel_protocol import validate_taps  # noqa: E402
from sionna_moving import MovingSionnaScene  # noqa: E402
from sionna_moving import analyze_phase_progression  # noqa: E402
from sionna_moving import tap_changes  # noqa: E402
from sionna_radio_config import load_radio_config  # noqa: E402
from sionna_stationary import load_scene_config  # noqa: E402
from sionna_taps import interpolate_taps  # noqa: E402
from sionna_taps import taps_from_report  # noqa: E402
from trajectory import load_trajectory  # noqa: E402
from trajectory import radio_motion_metrics  # noqa: E402
from trajectory import translate_trajectory  # noqa: E402


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def percentile(values, fraction):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile requires values")
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def protocol_taps(point_report):
    conversion = point_report["conversion"]
    if not conversion["safe_to_send"]:
        raise ValueError("Sionna result is not safe to send")
    if conversion["normalization"] != "none":
        raise ValueError("movement test requires absolute coefficients")
    if not conversion["absolute_coefficients_preserved"]:
        raise ValueError("Sionna complex coefficients were not preserved")
    taps = tuple(
        ProtocolTap(tap.delay, tap.coefficient)
        for tap in taps_from_report(conversion)
    )
    return validate_taps(taps)


def dry_run_gate(report):
    errors = []
    points = report.get("points", [])
    if len(points) != 21:
        errors.append("dry run must contain exactly 21 positions")
    for point in points:
        if not point.get("conversion", {}).get("safe_to_send"):
            errors.append(f"position {point.get('index')} is invalid")
        actual_gnb = point.get("transmitter_position", [])
        expected_gnb = report["stationary_gnb"]
        if (
            len(actual_gnb) != len(expected_gnb)
            or any(
                not math.isclose(
                    float(actual),
                    float(expected),
                    rel_tol=0.0,
                    abs_tol=1e-6,
                )
                for actual, expected in zip(actual_gnb, expected_gnb)
            )
        ):
            errors.append(f"position {point.get('index')} changed the gNB position")
    warm = points[1:]
    if warm:
        combined = [
            point["timing_ms"]["solve"]
            + point["timing_ms"]["conversion"]
            for point in warm
        ]
        if percentile(combined, 0.99) > 25.0:
            errors.append("99th-percentile solve+conversion exceeds 25 ms")
        if max(point["timing_ms"]["total"] for point in warm) > 40.0:
            errors.append("a moving-position calculation exceeds 40 ms")
    if not report.get("phase_progression", {}).get("safe"):
        errors.extend(report["phase_progression"].get("errors", []))
    return {"safe": not errors, "errors": errors}


def run_dry(args, radio, trajectory, config):
    scene = MovingSionnaScene(
        config,
        carrier_hz=radio.carrier_hz,
        sample_rate=radio.sample_rate,
    )
    points = []
    previous = None
    for point in trajectory.points:
        result = scene.solve(point)
        result["changes"] = tap_changes(previous, result)
        points.append(result)
        previous = result
    phase = analyze_phase_progression(points, radio.carrier_hz)
    motion = radio_motion_metrics(
        radio.carrier_hz,
        trajectory.points[0].speed_mps,
        trajectory.update_interval_ns,
    )
    report = {
        "schema_version": 1,
        "mode": "moving-channel-complete-dry-run",
        "created_monotonic_ns": time.monotonic_ns(),
        "trajectory": trajectory.name,
        "update_interval_ns": trajectory.update_interval_ns,
        "carrier_hz": radio.carrier_hz,
        "sample_rate": radio.sample_rate,
        "stationary_gnb": list(trajectory.points[0].position),
        "configured_gnb": config["transmitter"]["position"],
        "scene_setup_ms": scene.scene_setup_ns / 1e6,
        "motion": motion,
        "noise_enabled": False,
        "artificial_doppler": False,
        "points": points,
        "phase_progression": phase,
    }
    report["stationary_gnb"] = config["transmitter"]["position"]
    report["gate"] = dry_run_gate(report)
    output = pathlib.Path(args.output)
    write_json(output, report)
    print(json.dumps({
        "output": str(output),
        "sha256": sha256(output),
        "gate": report["gate"],
        "scene_setup_ms": report["scene_setup_ms"],
        "solve_ms": [p["timing_ms"]["solve"] for p in points],
        "conversion_ms": [p["timing_ms"]["conversion"] for p in points],
    }, sort_keys=True), flush=True)
    if not report["gate"]["safe"]:
        raise SystemExit(2)


def validate_dry_report(path, expected_sha, radio, trajectory):
    if expected_sha and sha256(path) != expected_sha:
        raise ValueError("dry-run checksum does not match")
    report = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    gate = dry_run_gate(report)
    if not gate["safe"]:
        raise ValueError("dry-run gate failed: " + "; ".join(gate["errors"]))
    if report["trajectory"] != trajectory.name:
        raise ValueError("dry-run trajectory does not match")
    if float(report["carrier_hz"]) != radio.carrier_hz:
        raise ValueError("dry-run carrier does not match")
    if float(report["sample_rate"]) != radio.sample_rate:
        raise ValueError("dry-run sample rate does not match")
    return report


def stream_cir(client, taps, sequence):
    # Stream one latest-wins CIR
    message = build_update(
        taps=taps,
        sequence=sequence,
        direction="both",
        client_send_ns=time.time_ns(),
    )
    client.stream(message)


def blend_to_protocol(previous_taps, current_taps, alpha):
    return tuple(
        ProtocolTap(tap.delay, tap.coefficient)
        for tap in interpolate_taps(previous_taps, current_taps, alpha)
    )


def run_live(args, radio, trajectory, config):
    validate_dry_report(
        args.dry_run_report,
        args.expected_dry_run_sha256,
        radio,
        trajectory,
    )
    scene = MovingSionnaScene(
        config,
        carrier_hz=radio.carrier_hz,
        sample_rate=radio.sample_rate,
    )
    client = ChannelClient(args.endpoint, stream_endpoint=args.stream_endpoint)
    records = []
    try:
        config_response = client.get_config()
        if float(config_response["sample_rate"]) != radio.sample_rate:
            raise ValueError("live sample rate does not match")

        steps = max(1, int(args.interp_steps))
        step_sleep_s = trajectory.update_interval_ns / steps / 1e9
        sequence = int(client.get_status()["last_accepted_sequence"]) + 1

        # starting-position CIR
        previous_taps = protocol_taps(scene.solve(trajectory.points[0]))
        stream_cir(client, previous_taps, sequence)
        records.append({
            "index": 0,
            "alpha": 1.0,
            "tap_count": len(previous_taps),
        })
        sequence += 1

        epoch_created_ns = time.monotonic_ns()
        for index in range(1, len(trajectory.points)):
            current_taps = protocol_taps(scene.solve(trajectory.points[index]))
            # stream interpolated CIRs across the trajectory interval
            for step in range(1, steps + 1):
                alpha = step / steps
                blended = blend_to_protocol(
                    previous_taps, current_taps, alpha
                )
                stream_cir(client, blended, sequence)
                records.append({
                    "index": index,
                    "alpha": alpha,
                    "tap_count": len(blended),
                })
                sequence += 1
                time.sleep(step_sleep_s)
            previous_taps = current_taps

        time.sleep(args.final_hold_seconds)
        final_status = client.get_status()
        result = {
            "schema_version": 1,
            "mode": "live-moving-ue-channel-stream",
            "dry_run_report": str(args.dry_run_report),
            "dry_run_sha256": sha256(args.dry_run_report),
            "update_interval_ns": trajectory.update_interval_ns,
            "interp_steps": steps,
            "per_symbol_channels": True,
            "noise_enabled": False,
            "streamed_updates": len(records),
            "epoch_created_monotonic_ns": epoch_created_ns,
            "records": records,
            "final_status": final_status,
        }
        write_json(args.output, result)
        print(json.dumps({
            "output": args.output,
            "streamed_updates": len(records),
            "final_accepted_sequence":
                final_status["last_accepted_sequence"],
        }, sort_keys=True), flush=True)
    finally:
        client.close()


def parse_args():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument(
        "--trajectory",
        default=str(REPO_ROOT / "channel_emulation/trajectories/default_trajectory.json"),
    )
    parser.add_argument(
        "--scene-config",
        default=str(REPO_ROOT / "channel_emulation/scenes/default_scene.json"),
    )
    parser.add_argument(
        "--gnb-config",
        default=str(REPO_ROOT / "configs/srsRAN/srsran-gnb/config/srsran-gnb.yaml"),
    )
    parser.add_argument(
        "--ue-config",
        default=str(REPO_ROOT / "configs/ues/srsue/config/ue0.conf"),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--dry-run-report")
    parser.add_argument("--expected-dry-run-sha256")
    parser.add_argument("--placement-mode", choices=["configured", "random"])
    parser.add_argument("--placement-seed", type=int)
    parser.add_argument("--placement-min-distance", type=float)
    parser.add_argument("--endpoint", default=os.environ.get("CHANNEL_CONTROL_ENDPOINT", "tcp://127.0.0.1:5555"))
    parser.add_argument("--stream-endpoint", default=os.environ.get("CHANNEL_STREAM_ENDPOINT", "tcp://127.0.0.1:5556"))
    parser.add_argument("--interp-steps", type=int, default=8)
    parser.add_argument("--final-hold-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.live and not args.dry_run_report:
        parser.error("--live requires --dry-run-report")
    return args


def main():
    args = parse_args()
    radio = load_radio_config(args.gnb_config, args.ue_config)
    trajectory = load_trajectory(args.trajectory)
    config = load_scene_config(
        args.scene_config,
        placement_mode=args.placement_mode,
        placement_seed=args.placement_seed,
        min_distance_m=args.placement_min_distance,
    )
    resolved = config.get("resolved_placement") or {}
    if resolved.get("mode") == "random":
        # random mode shifts trajectory to sampled start
        start = config["receiver"]["position"]
        offset = tuple(
            float(start_coord) - float(point_coord)
            for start_coord, point_coord in zip(start, trajectory.points[0].position)
        )
        trajectory = translate_trajectory(trajectory, offset)
        config["receiver"]["position"] = list(trajectory.points[0].position)
        config["resolved_placement"]["receiver"] = list(trajectory.points[0].position)
        config["resolved_placement"]["trajectory_offset"] = list(offset)
    if tuple(config["receiver"]["position"]) != trajectory.points[0].position:
        raise ValueError("scene receiver must match trajectory position 0")
    if args.dry_run:
        run_dry(args, radio, trajectory, config)
    else:
        run_live(args, radio, trajectory, config)


if __name__ == "__main__":
    main()
