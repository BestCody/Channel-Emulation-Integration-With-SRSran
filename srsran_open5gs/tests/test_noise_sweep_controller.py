import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "channel_emulation"))
sys.path.insert(0, str(REPO_ROOT / "configs/ues/srsue-live/config"))

from noise_sweep_controller import build_plan  # noqa: E402
from noise_sweep_controller import level_from_plan  # noqa: E402
from noise_sweep_controller import median_signal  # noqa: E402


class NoiseSweepControllerTests(unittest.TestCase):
    def test_build_plan_solves_sigma_per_direction(self):
        report = {
            "downlink_signal_power": 1.0,
            "uplink_signal_power": 0.25,
        }
        plan = build_plan(report, [20.0, 0.0])
        level20 = level_from_plan(plan, 20.0)
        self.assertAlmostEqual(level20["downlink"]["noise_sigma"], 0.1)
        self.assertAlmostEqual(level20["uplink"]["noise_sigma"], 0.05)
        level0 = level_from_plan(plan, 0.0)
        self.assertAlmostEqual(level0["downlink"]["noise_sigma"], 1.0)

    def test_level_from_plan_missing_raises(self):
        plan = build_plan(
            {"downlink_signal_power": 1.0, "uplink_signal_power": 1.0},
            [10.0],
        )
        with self.assertRaises(ValueError):
            level_from_plan(plan, 5.0)

    def test_median_signal_filters_nonpositive(self):
        samples = [
            {"downlink_signal": 0.04},
            {"downlink_signal": None},
            {"downlink_signal": 0.06},
        ]
        self.assertAlmostEqual(median_signal(samples, "downlink"), 0.05)

    def test_median_signal_requires_samples(self):
        with self.assertRaises(ValueError):
            median_signal([{"downlink_signal": None}], "downlink")

    def test_live_client_uses_stream_endpoint(self):
        source = (
            REPO_ROOT / "channel_emulation/noise_sweep_controller.py"
        ).read_text(encoding="utf-8")
        self.assertIn("--stream-endpoint", source)
        self.assertIn("stream_endpoint=args.stream_endpoint", source)


if __name__ == "__main__":
    unittest.main()
