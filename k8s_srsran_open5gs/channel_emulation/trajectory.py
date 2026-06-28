import json
import math
from dataclasses import dataclass
from pathlib import Path

SPEED_OF_LIGHT = 299_792_458.0

@dataclass(frozen=True)
class TrajectoryPoint:
    index: int
    time_ns: int
    position: tuple
    velocity: tuple
    speed_mps: float


@dataclass(frozen=True)
class Trajectory:
    name: str
    update_interval_ns: int
    points: tuple

def _vector(value, name):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must contain three values")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{name} must contain finite values")
    return result


def _norm(value):
    return math.sqrt(sum(item * item for item in value))


def _distance(first, second):
    return _norm(tuple(b - a for a, b in zip(first, second)))


def load_trajectory(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported trajectory schema")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("trajectory name is required")
    interval_ms = float(data.get("update_interval_ms"))
    if not math.isfinite(interval_ms) or interval_ms <= 0.0:
        raise ValueError("update interval must be finite and positive")
    interval_ns = int(round(interval_ms * 1_000_000.0))
    raw_points = data.get("points")
    if not isinstance(raw_points, list) or len(raw_points) < 2:
        raise ValueError("trajectory requires at least two points")

    points = []
    for expected_index, raw in enumerate(raw_points):
        if not isinstance(raw, dict):
            raise ValueError("trajectory point must be an object")
        index = raw.get("index")
        if index != expected_index:
            raise ValueError("trajectory indexes must be consecutive")
        time_ms = float(raw.get("time_ms"))
        time_ns = int(round(time_ms * 1_000_000.0))
        expected_time = expected_index * interval_ns
        if time_ns != expected_time:
            raise ValueError("trajectory timestamps must match the interval")
        position = _vector(raw.get("position"), "position")
        velocity = _vector(raw.get("velocity"), "velocity")
        speed = float(raw.get("speed_mps"))
        if not math.isfinite(speed) or speed < 0.0:
            raise ValueError("speed must be finite and non-negative")
        if not math.isclose(_norm(velocity), speed, rel_tol=0, abs_tol=1e-9):
            raise ValueError("velocity magnitude does not match speed")
        points.append(
            TrajectoryPoint(index, time_ns, position, velocity, speed)
        )

    if points[0].time_ns != 0:
        raise ValueError("starting position must have timestamp zero")
    for previous, current in zip(points, points[1:]):
        expected_distance = current.speed_mps * interval_ns / 1e9
        actual_distance = _distance(previous.position, current.position)
        if not math.isclose(
            actual_distance,
            expected_distance,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError("position step does not match speed and interval")

    return Trajectory(name, interval_ns, tuple(points))


def radio_motion_metrics(carrier_hz, speed_mps, interval_ns):
    carrier_hz = float(carrier_hz)
    speed_mps = float(speed_mps)
    if carrier_hz <= 0.0 or not math.isfinite(carrier_hz):
        raise ValueError("carrier frequency must be positive")
    if speed_mps < 0.0 or not math.isfinite(speed_mps):
        raise ValueError("speed must be non-negative")
    wavelength = SPEED_OF_LIGHT / carrier_hz
    doppler_hz = speed_mps / wavelength
    interval_s = int(interval_ns) / 1e9
    return {
        "wavelength_m": wavelength,
        "maximum_doppler_hz": doppler_hz,
        "movement_per_update_m": speed_mps * interval_s,
        "phase_change_rad": 2.0 * math.pi * doppler_hz * interval_s,
        "phase_change_degrees": 360.0 * doppler_hz * interval_s,
        "coherence_time_seconds": (
            math.inf if doppler_hz == 0.0 else 0.423 / doppler_hz
        ),
    }


def activation_sample(
    first_movement_sample,
    point_index,
    update_interval_ns,
    sample_rate,
):
    if point_index < 1:
        raise ValueError("movement activation is only for positions 1+")
    interval_from_first_ns = (point_index - 1) * int(update_interval_ns)
    return int(first_movement_sample) + int(
        round(interval_from_first_ns * float(sample_rate) / 1e9)
    )