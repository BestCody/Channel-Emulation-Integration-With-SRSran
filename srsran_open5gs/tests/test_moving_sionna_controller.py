import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "channel_emulation"))
sys.path.insert(0, str(REPO_ROOT / "configs/ues/srsue-live/config"))

from moving_sionna_controller import dry_run_gate  # noqa: E402
from moving_sionna_controller import protocol_taps  # noqa: E402


def valid_point(index, total=8.0, solve=7.9, conversion=0.1):
    return {
        "index": index,
        "transmitter_position": [-0.05, 0.0, 10.0],
        "timing_ms": {
            "solve": solve,
            "conversion": conversion,
            "total": total,
        },
        "conversion": {
            "safe_to_send": True,
            "normalization": "none",
            "absolute_coefficients_preserved": True,
            "retained_taps": [
                {"delay": 0, "real": 0.25, "imag": -0.5}
            ],
        },
    }


class MovingControllerTests(unittest.TestCase):
    def test_dry_run_gate_accepts_complete_fast_report(self):
        report = {
            "stationary_gnb": [-0.05, 0.0, 10.0],
            "points": [valid_point(index) for index in range(21)],
            "phase_progression": {"safe": True, "errors": []},
        }
        report["points"][0]["transmitter_position"][0] = -0.05000000074505806
        self.assertTrue(dry_run_gate(report)["safe"])

    def test_dry_run_gate_rejects_real_gnb_movement(self):
        report = {
            "stationary_gnb": [-0.05, 0.0, 10.0],
            "points": [valid_point(index) for index in range(21)],
            "phase_progression": {"safe": True, "errors": []},
        }
        report["points"][4]["transmitter_position"][0] = -0.049
        gate = dry_run_gate(report)
        self.assertFalse(gate["safe"])
        self.assertIn("position 4 changed the gNB position", gate["errors"])

    def test_dry_run_gate_rejects_slow_or_phase_failure(self):
        report = {
            "stationary_gnb": [-0.05, 0.0, 10.0],
            "points": [valid_point(index) for index in range(21)],
            "phase_progression": {"safe": False, "errors": ["phase"]},
        }
        report["points"][5]["timing_ms"]["total"] = 41.0
        gate = dry_run_gate(report)
        self.assertFalse(gate["safe"])
        self.assertIn("phase", gate["errors"])

    def test_protocol_taps_preserve_complex_phase(self):
        taps = protocol_taps(valid_point(0))
        self.assertEqual(taps[0].coefficient, 0.25 - 0.5j)

    def test_controller_source_streams_interpolated_cirs(self):
        text = (
            REPO_ROOT / "channel_emulation/moving_sionna_controller.py"
        ).read_text()
        self.assertIn("stream_cir", text)
        self.assertIn("interpolate_taps", text)
        self.assertIn("--stream-endpoint", text)
        self.assertIn("stream_endpoint=args.stream_endpoint", text)
        self.assertIn('"per_symbol_channels": True', text)
        self.assertIn('"noise_enabled": False', text)
        # the keyframe/transaction model is gone
        self.assertNotIn("activate_at_sample", text)
        self.assertNotIn("wait_for_activation", text)
        self.assertNotIn("piecewise_constant", text)
        self.assertNotIn("noise_update", text)


if __name__ == "__main__":
    unittest.main()
