import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "channel_emulation"))

from trajectory import activation_sample  # noqa: E402
from trajectory import load_trajectory  # noqa: E402
from trajectory import radio_motion_metrics  # noqa: E402


TRAJECTORY = (
    REPO_ROOT
    / "channel_emulation/trajectories/default_trajectory.json"
)


class TrajectoryTests(unittest.TestCase):
    def test_exact_short_straight_trajectory(self):
        trajectory = load_trajectory(TRAJECTORY)
        self.assertEqual(len(trajectory.points), 21)
        self.assertEqual(trajectory.update_interval_ns, 50_000_000)
        self.assertEqual(trajectory.points[0].position, (0.05, 0.0, 10.0))
        self.assertEqual(trajectory.points[-1].position, (0.25, 0.0, 10.0))
        self.assertEqual(trajectory.points[-1].time_ns, 1_000_000_000)
        self.assertTrue(all(point.speed_mps == 0.2 for point in trajectory.points))

    def test_radio_motion_metrics(self):
        values = radio_motion_metrics(1_842_500_000, 0.2, 50_000_000)
        self.assertAlmostEqual(values["wavelength_m"], 0.16270961085481683)
        self.assertAlmostEqual(values["maximum_doppler_hz"], 1.2291836908051903)
        self.assertAlmostEqual(values["movement_per_update_m"], 0.01)
        self.assertAlmostEqual(values["phase_change_rad"], 0.38615944529459734)

    def test_activation_samples_do_not_shift_when_positions_skip(self):
        first = 10_000_000
        rate = 23_040_000
        interval = 50_000_000
        self.assertEqual(activation_sample(first, 1, interval, rate), first)
        self.assertEqual(
            activation_sample(first, 2, interval, rate),
            first + 1_152_000,
        )
        self.assertEqual(
            activation_sample(first, 20, interval, rate),
            first + 19 * 1_152_000,
        )


if __name__ == "__main__":
    unittest.main()
