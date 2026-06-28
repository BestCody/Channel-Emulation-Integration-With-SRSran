import cmath
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "channel_emulation"))

from sionna_moving import analyze_phase_progression  # noqa: E402
from sionna_moving import _point_json  # noqa: E402
from sionna_moving import tap_changes  # noqa: E402
from trajectory import SPEED_OF_LIGHT  # noqa: E402


def point(index, delay, carrier=1_842_500_000, taps=None):
    coefficient = cmath.exp(-1j * 2.0 * cmath.pi * carrier * delay)
    retained = taps or [
        {"delay": 0, "real": coefficient.real, "imag": coefficient.imag}
    ]
    return {
        "index": index,
        "conversion": {
            "original_paths": [{
                "index": 0,
                "status": "valid",
                "delay_seconds": delay,
                "rounded_sample_delay": 0,
                "coefficient": {
                    "real": coefficient.real,
                    "imag": coefficient.imag,
                },
                "power": 1.0,
            }],
            "retained_taps": retained,
        },
    }


class SionnaMovingTests(unittest.TestCase):
    def test_point_json_reads_one_element_drjit_components(self):
        point_value = type("Point", (), {
            "x": [1.25],
            "y": [-2.5],
            "z": [10.0],
        })()
        self.assertEqual(_point_json(point_value), [1.25, -2.5, 10.0])

    def test_phase_progression_matches_geometric_delay(self):
        reports = [
            point(index, (0.1 + 0.01 * index) / SPEED_OF_LIGHT)
            for index in range(21)
        ]
        result = analyze_phase_progression(reports, 1_842_500_000)
        self.assertTrue(result["safe"], result["errors"])
        deltas = [
            record["actual_phase_delta_rad"]
            for record in result["records"][1:]
        ]
        self.assertTrue(all(delta < 0.0 for delta in deltas))
        self.assertLess(
            max(abs(record["phase_error_rad"]) for record in result["records"][1:]),
            1e-10,
        )

    def test_tap_appearance_disappearance_and_change(self):
        previous = point(0, 0.0, taps=[
            {"delay": 0, "real": 1.0, "imag": 0.0},
            {"delay": 2, "real": 0.1, "imag": 0.0},
        ])
        current = point(1, 0.0, taps=[
            {"delay": 0, "real": 0.9, "imag": 0.1},
            {"delay": 3, "real": 0.1, "imag": 0.0},
        ])
        changes = tap_changes(previous, current)
        self.assertEqual(changes["appeared_delays"], [3])
        self.assertEqual(changes["disappeared_delays"], [2])
        self.assertEqual(changes["changed_delays"], [0])


if __name__ == "__main__":
    unittest.main()
