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
from sionna_taps import taps_from_report  # noqa: E402
from trajectory import activation_sample  # noqa: E402
from trajectory import load_trajectory  # noqa: E402
from trajectory import radio_motion_metrics  # noqa: E402
from trajectory import translate_trajectory  # noqa: E402


NO_PENDING_SEQUENCE = (1 << 64) - 1


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


def wait_for_activation(client, sequence, timeout_seconds=3.0):
    start_ns = time.monotonic_ns()
    deadline_ns = start_ns + int(timeout_seconds * 1e9)
    while time.monotonic_ns() < deadline_ns:
        status = client.get_status()
        if (
            status["downlink"]["active_sequence"] == sequence
            and status["uplink"]["active_sequence"] == sequence
        ):
            return status, time.monotonic_ns() - start_ns
        time.sleep(0.005)
    raise TimeoutError(f"sequence {sequence} did not activate")


def schedule_result(client, report, sequence, activate_at_sample):
    taps = protocol_taps(report)
    message = build_update(
        taps=taps,
        sequence=sequence,
        activate_at_sample=activate_at_sample,
        direction="both",
        client_send_ns=time.time_ns(),
    )
    request_start_ns = time.monotonic_ns()
    ack = client.request(message)
    request_end_ns = time.monotonic_ns()
    return {
        "sequence": sequence,
        "tap_count": len(taps),
        "requested_activation_sample": activate_at_sample,
        "request_start_monotonic_ns": request_start_ns,
        "request_end_monotonic_ns": request_end_ns,
        "ack_rtt_ms": (request_end_ns - request_start_ns) / 1e6,
        "ack": ack,
    }


def pending_present(status):
    return (
        status["downlink"]["pending_sequence"] is not None
        or status["uplink"]["pending_sequence"] is not None
    )


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


def run_live(args, radio, trajectory, config):
    dry = validate_dry_report(
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
    client = ChannelClient(args.endpoint)
    records = []
    skipped = []
    completed = []
    consecutive_failures = 0
    try:
        config_response = client.get_config()
        if float(config_response["sample_rate"]) != radio.sample_rate:
            raise ValueError("live sample rate does not match")

        # start-position channel confirmed before the epoch
        start_result = scene.solve(trajectory.points[0])
        before = client.get_status()
        start_sequence = int(before["last_accepted_sequence"]) + 1
        start_sample = max(
            before["downlink"]["sample_count"],
            before["uplink"]["sample_count"],
        ) + int(round(0.100 * radio.sample_rate))
        start_schedule = schedule_result(
            client,
            start_result,
            start_sequence,
            start_sample,
        )
        start_active, start_wait_ns = wait_for_activation(
            client,
            start_sequence,
        )
        start_record = {
            **start_result,
            "status": "starting-position-confirmed",
            "schedule": start_schedule,
            "activation_wait_ms": start_wait_ns / 1e6,
            "downlink_activation_error_samples":
                start_active["downlink"]["actual_activation_sample"]
                - start_sample,
            "uplink_activation_error_samples":
                start_active["uplink"]["actual_activation_sample"]
                - start_sample,
            "downlink_activation_time_ns":
                start_active["downlink"]["activation_time_ns"],
            "uplink_activation_time_ns":
                start_active["uplink"]["activation_time_ns"],
        }
        records.append(start_record)
        completed.append(0)

        # movement epoch starts here; targets stay fixed
        epoch_created_ns = time.monotonic_ns()
        epoch_status = client.get_status()
        first_movement_sample = max(
            epoch_status["downlink"]["sample_count"],
            epoch_status["uplink"]["sample_count"],
        ) + int(round(args.movement_lead_ms * radio.sample_rate / 1000.0))
        first_movement_deadline_ns = epoch_created_ns + int(
            args.movement_lead_ms * 1_000_000
        )
        margin_samples = int(round(args.late_margin_ms * radio.sample_rate / 1000.0))
        margin_ns = int(args.late_margin_ms * 1_000_000)

        current_result = scene.solve(trajectory.points[1])
        next_sequence = start_sequence + 1
        for index in range(1, len(trajectory.points)):
            point = trajectory.points[index]
            target_sample = activation_sample(
                first_movement_sample,
                index,
                trajectory.update_interval_ns,
                radio.sample_rate,
            )
            target_deadline_ns = first_movement_deadline_ns + (
                index - 1
            ) * trajectory.update_interval_ns
            record = {
                **current_result,
                "target_activation_monotonic_ns": target_deadline_ns,
                "target_activation_sample": target_sample,
            }
            status = client.get_status()
            current_sample = max(
                status["downlink"]["sample_count"],
                status["uplink"]["sample_count"],
            )
            reason = None
            if pending_present(status):
                reason = "previous update is still pending"
            elif not current_result["conversion"]["safe_to_send"]:
                reason = "invalid Sionna channel"
            elif current_result["calculation_end_monotonic_ns"] > (
                target_deadline_ns - margin_ns
            ):
                reason = "calculation finished late"
            elif time.monotonic_ns() > target_deadline_ns - margin_ns:
                reason = "position missed monotonic deadline"
            elif target_sample - current_sample < margin_samples:
                reason = "position missed sample deadline"

            scheduled = False
            if reason is None:
                try:
                    schedule = schedule_result(
                        client,
                        current_result,
                        next_sequence,
                        target_sample,
                    )
                    record["schedule"] = schedule
                    record["status"] = "scheduled"
                    scheduled = True
                    sequence = next_sequence
                    next_sequence += 1
                except Exception as error:
                    reason = f"schedule failed: {error}"

            # precompute next channel while this one runs
            next_result = None
            if index + 1 < len(trajectory.points):
                try:
                    next_result = scene.solve(trajectory.points[index + 1])
                except Exception as error:
                    next_result = {
                        "index": index + 1,
                        "trajectory_time_ns": trajectory.points[index + 1].time_ns,
                        "position": list(trajectory.points[index + 1].position),
                        "velocity": list(trajectory.points[index + 1].velocity),
                        "speed_mps": trajectory.points[index + 1].speed_mps,
                        "calculation_start_monotonic_ns": time.monotonic_ns(),
                        "calculation_end_monotonic_ns": time.monotonic_ns(),
                        "timing_ms": {"position_update": 0.0, "solve": 0.0, "cir_extraction": 0.0, "conversion": 0.0, "total": 0.0},
                        "conversion": {"safe_to_send": False, "errors": [str(error)], "original_paths": [], "retained_taps": []},
                    }

            if scheduled:
                try:
                    active, wait_ns = wait_for_activation(client, sequence)
                    record["status"] = "activated"
                    record["activation_wait_ms"] = wait_ns / 1e6
                    record["downlink_activation_error_samples"] = (
                        active["downlink"]["actual_activation_sample"]
                        - target_sample
                    )
                    record["uplink_activation_error_samples"] = (
                        active["uplink"]["actual_activation_sample"]
                        - target_sample
                    )
                    record["downlink_activation_time_ns"] = active["downlink"]["activation_time_ns"]
                    record["uplink_activation_time_ns"] = active["uplink"]["activation_time_ns"]
                    completed.append(index)
                    consecutive_failures = 0
                except Exception as error:
                    reason = f"activation failed: {error}"

            if reason is not None:
                record["status"] = "skipped"
                record["skip_reason"] = reason
                skipped.append(index)
                consecutive_failures += 1
            records.append(record)
            if consecutive_failures >= 3:
                break
            current_result = next_result

        hold_start_ns = time.monotonic_ns()
        time.sleep(args.final_hold_seconds)
        final_status = client.get_status()
        phase = analyze_phase_progression(
            [record for record in records if "conversion" in record],
            radio.carrier_hz,
        )
        for previous, current in zip([None] + records[:-1], records):
            current["changes"] = tap_changes(previous, current)
        result = {
            "schema_version": 1,
            "mode": "live-moving-ue-channel",
            "dry_run_report": str(args.dry_run_report),
            "dry_run_sha256": sha256(args.dry_run_report),
            "starting_position_confirmed_before_epoch": True,
            "epoch_created_monotonic_ns": epoch_created_ns,
            "first_movement_activation_monotonic_ns": first_movement_deadline_ns,
            "first_movement_activation_sample": first_movement_sample,
            "update_interval_ns": trajectory.update_interval_ns,
            "piecewise_constant": True,
            "noise_enabled": False,
            "artificial_doppler": False,
            "completed_positions": completed,
            "skipped_positions": skipped,
            "records": records,
            "phase_progression": phase,
            "final_hold_start_monotonic_ns": hold_start_ns,
            "final_status": final_status,
        }
        write_json(args.output, result)
        print(json.dumps({
            "output": args.output,
            "completed_positions": completed,
            "skipped_positions": skipped,
            "final_active_sequences": [
                final_status["downlink"]["active_sequence"],
                final_status["uplink"]["active_sequence"],
            ],
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
    parser.add_argument("--placement-max-attempts", type=int)
    parser.add_argument("--placement-min-distance", type=float)
    parser.add_argument("--endpoint", default=os.environ.get("CHANNEL_CONTROL_ENDPOINT", "tcp://127.0.0.1:5555"))
    parser.add_argument("--movement-lead-ms", type=float, default=250.0)
    parser.add_argument("--late-margin-ms", type=float, default=10.0)
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
        max_attempts=args.placement_max_attempts,
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