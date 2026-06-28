import json
import math
import pathlib
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(REPO_ROOT / "channel_emulation"))

from noise_math import load_noise_calibration  # noqa: E402
from noise_math import measured_snr_db  # noqa: E402


CALIBRATION = (
    REPO_ROOT / "channel_emulation/noise_calibration_gr381.json"
)


class NoiseMathTests(unittest.TestCase):
    def setUp(self):
        self.calibration = load_noise_calibration(CALIBRATION)

    def test_uses_measured_formula(self):
        self.assertAlmostEqual(
            self.calibration.scale,
            1.0002560835932446,
        )
        self.assertAlmostEqual(
            self.calibration.exponent,
            2.000099170619678,
        )
        expected = (
            self.calibration.scale
            * 0.5 ** self.calibration.exponent
        )
        self.assertAlmostEqual(
            self.calibration.power_from_amplitude(0.5),
            expected,
        )

    def test_inverse_calculation(self):
        amplitude = 0.25
        power = self.calibration.power_from_amplitude(amplitude)
        self.assertAlmostEqual(
            self.calibration.amplitude_from_power(power),
            amplitude,
            places=12,
        )

    def test_snr_calculation(self):
        result = self.calibration.amplitude_for_snr(0.04, 20.0)
        self.assertAlmostEqual(result["target_noise_power"], 0.0004)
        self.assertAlmostEqual(
            measured_snr_db(
                result["signal_power"],
                result["target_noise_power"],
            ),
            20.0,
        )

    def test_maximum_amplitude_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds maximum"):
            self.calibration.amplitude_from_power(512.0 ** 2 * 1.1)
        with self.assertRaisesRegex(ValueError, "exceeds maximum"):
            self.calibration.power_from_amplitude(512.01)

    def test_calibration_metadata_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "image"):
            load_noise_calibration(
                CALIBRATION,
                expected_image_id="sha256:wrong",
            )

    def test_unstable_calibration_rejected(self):
        data = json.loads(CALIBRATION.read_text())
        data["fit"]["r_squared"] = 0.9
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "not stable"):
                load_noise_calibration(path)

    def test_zero_noise_snr_is_infinite(self):
        self.assertTrue(math.isinf(measured_snr_db(1.0, 0.0)))


if __name__ == "__main__":
    unittest.main()
