#!/usr/bin/env python3

import json
import pathlib
import time

from sionna_taps import convert_paths


EXPECTED_SIONNA_VERSION = "2.0.1"
EXPECTED_SIONNA_RT_VERSION = "2.0.1"
EXPECTED_VARIANT = "cuda_ad_mono_polarized"


def load_scene_config(path):
    config = json.loads(
        pathlib.Path(path).read_text(encoding="utf-8")
    )
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
    return config


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
    solver_options = dict(config["solver"])
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
        max_taps=48,
        max_delay=255,
        late_policy=config["conversion"]["late_policy"],
        normalization=config["conversion"]["normalization"],
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
