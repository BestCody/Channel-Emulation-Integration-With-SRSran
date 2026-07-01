#!/usr/bin/env python3

import copy
import json
import math
import pathlib
import random
import time

from sionna_taps import convert_paths


EXPECTED_SIONNA_VERSION = "2.0.1"
EXPECTED_SIONNA_RT_VERSION = "2.0.1"
EXPECTED_VARIANT = "cuda_ad_mono_polarized"

# Effects default off, mirror list in config.py
PROPAGATION_EFFECTS = (
    "los",
    "specular_reflection",
    "diffuse_reflection",
    "refraction",
    "diffraction",
    "edge_diffraction",
    "diffraction_lit_region",
)


def _solver_options(solver):
    """Resolve solver toggles with every effect defaulted off"""
    options = dict(solver or {})
    for effect in PROPAGATION_EFFECTS:
        options.setdefault(effect, False)
    return options


def _validate_scene_config(config):
    required = {
        "scene",
        "transmitter",
        "receiver",
        "antenna",
        "solver",
        "conversion",
    }
    missing = required - set(config)
    if missing:
        raise ValueError(f"scene config is missing {sorted(missing)}")


def _distance(first, second):
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second)))


def scene_bounding_box(scene_name):
    """Return the physical scene bounding box"""
    import mitsuba as mi  # noqa: F401  (importing sionna.rt selects the variant)
    from sionna.rt import load_scene
    from sionna.rt import scene as rt_scene

    scene_path = getattr(rt_scene, scene_name, None)
    if not isinstance(scene_path, str):
        raise ValueError(f"unknown bundled scene: {scene_name}")
    scene = load_scene(scene_path)
    bbox = scene.mi_scene.bbox()
    scene_min = bbox.min
    scene_max = bbox.max
    lower = [float(scene_min.x), float(scene_min.y), float(scene_min.z)]
    upper = [float(scene_max.x), float(scene_max.y), float(scene_max.z)]
    if any(not math.isfinite(value) for value in lower + upper):
        raise ValueError(f"scene {scene_name} has a non-finite bounding box")
    return lower, upper


def _random_point(lower, upper, rng):
    return [rng.uniform(lo, hi) for lo, hi in zip(lower, upper)]


def _apply_random_placement(
    config,
    bounds,
    *,
    placement_seed=None,
    min_distance_m=None,
):
    placement = config.get("placement", {})
    if not isinstance(placement, dict):
        raise ValueError("placement must be an object")
    seed = placement_seed if placement_seed is not None else placement.get("seed")
    lower, upper = bounds
    min_distance = float(
        min_distance_m if min_distance_m is not None else placement.get("min_distance_m", 0.0)
    )
    original = {
        "transmitter": copy.deepcopy(config["transmitter"].get("position")),
        "receiver": copy.deepcopy(config["receiver"].get("position")),
    }
    transmitter, receivers = sample_ue_positions(
        bounds, 1, seed=seed, min_distance=min_distance
    )
    receiver = receivers[0]
    config["transmitter"]["position"] = transmitter
    config["receiver"]["position"] = receiver
    config["resolved_placement"] = {
        "mode": "random",
        "seed": seed,
        "scene_bounds": {"min": lower, "max": upper},
        "original": original,
        "transmitter": transmitter,
        "receiver": receiver,
        "min_distance_m": min_distance,
    }
    return config


def sample_ue_positions(bounds, num_ues, *, seed=None, min_distance=0.0):
    """Sample one shared TX and num_ues RX positions

    Seeded rejection sampling; _apply_random_placement calls this
    with num_ues==1 for the single-link case.
    """
    num_ues = int(num_ues)
    if num_ues < 1:
        raise ValueError("num_ues must be at least one")
    lower, upper = bounds
    min_distance = float(min_distance)
    if min_distance < 0.0 or not math.isfinite(min_distance):
        raise ValueError("placement min_distance_m must be finite and non-negative")
    if min_distance > _distance(lower, upper):
        raise ValueError(
            "placement min_distance_m exceeds the scene bounding box; "
            "transmitter and receiver cannot be separated that far"
        )
    rng = random.Random(seed)
    transmitter = _random_point(lower, upper, rng)
    receivers = []
    for _ in range(num_ues):
        receiver = _random_point(lower, upper, rng)
        while _distance(transmitter, receiver) < min_distance:
            receiver = _random_point(lower, upper, rng)
        receivers.append(receiver)
    return transmitter, receivers


def load_scene_config(
    path,
    *,
    placement_mode=None,
    placement_seed=None,
    min_distance_m=None,
    scene_bounds=None,
):
    config = json.loads(
        pathlib.Path(path).read_text(encoding="utf-8")
    )
    _validate_scene_config(config)
    config = copy.deepcopy(config)
    placement = config.get("placement", {})
    mode = placement_mode or placement.get("mode", "configured")
    if mode == "configured":
        config["resolved_placement"] = {
            "mode": "configured",
            "transmitter": config["transmitter"].get("position"),
            "receiver": config["receiver"].get("position"),
        }
        return config
    if mode == "random":
        bounds = scene_bounds or scene_bounding_box(config["scene"])
        return _apply_random_placement(
            config,
            bounds,
            placement_seed=placement_seed,
            min_distance_m=min_distance_m,
        )
    raise ValueError(f"unsupported placement mode: {mode}")


def _point(mi, values):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("position must contain three coordinates")
    return mi.Point3f(*(float(value) for value in values))


def _complex_array(value):
    import numpy as np

    if isinstance(value, tuple) and len(value) == 2:
        return np.asarray(value[0]) + 1j * np.asarray(value[1])
    return np.asarray(value)


def calculate_stationary_channel(
    config,
    *,
    carrier_hz,
    sample_rate,
    repeats=3,
):
    import numpy as np
    import drjit as dr
    import mitsuba as mi
    import sionna
    import sionna.rt
    from sionna.rt import (
        PathSolver,
        PlanarArray,
        Receiver,
        Transmitter,
        load_scene,
    )
    from sionna.rt import scene as rt_scene

    if sionna.__version__ != EXPECTED_SIONNA_VERSION:
        raise RuntimeError(
            f"expected sionna {EXPECTED_SIONNA_VERSION}, "
            f"found {sionna.__version__}"
        )
    if sionna.rt.__version__ != EXPECTED_SIONNA_RT_VERSION:
        raise RuntimeError(
            f"expected sionna-rt {EXPECTED_SIONNA_RT_VERSION}, "
            f"found {sionna.rt.__version__}"
        )
    if mi.variant() != EXPECTED_VARIANT:
        raise RuntimeError(
            f"expected Mitsuba variant {EXPECTED_VARIANT}, "
            f"found {mi.variant()}"
        )

    scene_name = config["scene"]
    scene_path = getattr(rt_scene, scene_name, None)
    if not isinstance(scene_path, str):
        raise ValueError(f"unknown bundled scene: {scene_name}")

    scene_started = time.perf_counter_ns()
    scene = load_scene(scene_path)
    scene.frequency = float(carrier_hz)
    antenna = config["antenna"]
    scene.tx_array = PlanarArray(
        num_rows=1,
        num_cols=1,
        pattern=antenna["pattern"],
        polarization=antenna["polarization"],
    )
    scene.rx_array = PlanarArray(
        num_rows=1,
        num_cols=1,
        pattern=antenna["pattern"],
        polarization=antenna["polarization"],
    )
    transmitter = Transmitter(
        name="gnb",
        position=_point(mi, config["transmitter"]["position"]),
    )
    receiver = Receiver(
        name="ue",
        position=_point(mi, config["receiver"]["position"]),
    )
    transmitter.look_at(receiver)
    receiver.look_at(transmitter)
    scene.add(transmitter)
    scene.add(receiver)
    scene.all_set(radio_map=False)
    dr.sync_thread()
    scene_ms = (time.perf_counter_ns() - scene_started) / 1_000_000

    solver = PathSolver()
    solver_options = _solver_options(config["solver"])
    solve_ms = []
    paths = None
    for _ in range(int(repeats)):
        started = time.perf_counter_ns()
        paths = solver(scene, **solver_options)
        dr.sync_thread()
        solve_ms.append(
            (time.perf_counter_ns() - started) / 1_000_000
        )

    cir_started = time.perf_counter_ns()
    coefficients, delays = paths.cir(
        sampling_frequency=float(sample_rate),
        num_time_steps=1,
        normalize_delays=False,
        out_type="numpy",
    )
    dr.sync_thread()
    cir_ms = (time.perf_counter_ns() - cir_started) / 1_000_000

    coefficient_array = _complex_array(coefficients)
    if coefficient_array.shape[-1] == 1:
        coefficient_array = coefficient_array[..., 0]
    coefficient_values = coefficient_array.reshape(-1)
    delay_values = np.asarray(delays).reshape(-1)
    if coefficient_values.size != delay_values.size:
        raise RuntimeError(
            "Sionna coefficient and delay shapes do not match"
        )

    conversion_started = time.perf_counter_ns()
    conversion = convert_paths(
        delay_values.tolist(),
        coefficient_values.tolist(),
        sample_rate,
        late_policy=config["conversion"]["late_policy"],
        normalization="none",
    )
    conversion_ms = (
        time.perf_counter_ns() - conversion_started
    ) / 1_000_000

    return {
        "sionna_version": sionna.__version__,
        "sionna_rt_version": sionna.rt.__version__,
        "mitsuba_variant": mi.variant(),
        "scene": scene_name,
        "carrier_hz": float(carrier_hz),
        "sample_rate": float(sample_rate),
        "transmitter": config["transmitter"],
        "receiver": config["receiver"],
        "placement": config.get("resolved_placement"),
        "antenna": config["antenna"],
        "solver": solver_options,
        "timing_ms": {
            "scene_setup": scene_ms,
            "solve": solve_ms,
            "cold_solve": solve_ms[0],
            "warm_solve_average": (
                sum(solve_ms[1:]) / len(solve_ms[1:])
                if len(solve_ms) > 1
                else None
            ),
            "cir_extraction": cir_ms,
            "conversion": conversion_ms,
        },
        "conversion": conversion,
    }
