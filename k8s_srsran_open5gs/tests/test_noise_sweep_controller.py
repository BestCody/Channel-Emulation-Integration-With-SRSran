import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "channel_emulation"))

from noise_math import load_noise_calibration  # noqa: E402
from noise_sweep_controller import build_plan  # noqa: E402
from noise_sweep_controller import summarize_direction  # noqa: E402


CALIBRATION = (
    REPO_ROOT / "channel_emulation/noise_calibration_gr381.json"
)


class NoiseSweepControllerTests(unittest.TestCase):
    def test_plan_freezes_independent_direction_amplitudes(self):
        calibration = load_noise_calibration(CALIBRATION)
        signal = {
            "downlink": {"signal_power": 0.04},
            "uplink": {"signal_power": 0.01},
        }
        plan = build_plan(signal, calibration, [20, 10])
        self.assertEqual(len(plan["levels"]), 2)
        first = plan["levels"][0]
        self.assertGreater(
            first["downlink"]["amplitude"],
            first["uplink"]["amplitude"],
        )
        self.assertEqual(first["target_snr_db"], 20.0)

    def test_plan_rejects_amplitude_above_limit(self):
        calibration = load_noise_calibration(CALIBRATION)
        signal = {
            "downlink": {"signal_power": 1_000_000.0},
            "uplink": {"signal_power": 1_000_000.0},
        }
        with self.assertRaisesRegex(ValueError, "exceeds maximum"):
            build_plan(signal, calibration, [0])

    def test_measurement_summary_uses_median(self):
        samples = [
            {
                "downlink_signal": value,
                "downlink_noise": 0.01,
            }
            for value in (1.0, 2.0, 100.0)
        ]
        result = summarize_direction(samples, "downlink")
        self.assertEqual(result["signal_power"], 2.0)
        self.assertAlmostEqual(result["measured_snr_db"], 23.0102999566)


if __name__ == "__main__":
    unittest.main()
