import math
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "channel_emulation"))

from noise_math import measured_snr_db  # noqa: E402
from noise_math import sigma_for_snr  # noqa: E402


class NoiseMathTests(unittest.TestCase):
    def test_measured_snr_db(self):
        self.assertAlmostEqual(measured_snr_db(1.0, 0.01), 20.0)

    def test_measured_snr_infinite_without_noise(self):
        self.assertEqual(measured_snr_db(1.0, 0.0), math.inf)

    def test_sigma_for_snr_power(self):
        result = sigma_for_snr(1.0, 20.0)
        self.assertAlmostEqual(result["target_noise_power"], 0.01)
        self.assertAlmostEqual(result["noise_sigma"], 0.1)

    def test_sigma_round_trips_to_target_snr(self):
        signal = 0.04
        for snr in (30.0, 10.0, 0.0):
            sigma = sigma_for_snr(signal, snr)["noise_sigma"]
            self.assertAlmostEqual(measured_snr_db(signal, sigma ** 2), snr)

    def test_rejects_nonpositive_signal(self):
        with self.assertRaises(ValueError):
            sigma_for_snr(0.0, 10.0)

    def test_rejects_nonfinite_snr(self):
        with self.assertRaises(ValueError):
            sigma_for_snr(1.0, math.inf)


if __name__ == "__main__":
    unittest.main()
