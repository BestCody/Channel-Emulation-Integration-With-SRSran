import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "configs/ues/srsue-noise/config"
sys.path.insert(0, str(CONFIG))

from noise_protocol import build_noise_update  # noqa: E402
from noise_protocol import parse_noise_update  # noqa: E402


class NoiseProtocolTests(unittest.TestCase):
    def test_both_direction_round_trip(self):
        message = build_noise_update(
            4,
            {"downlink": 0.1, "uplink": 0.2},
            direction="both",
        )
        update = parse_noise_update(message)
        self.assertEqual(update.sequence, 4)
        self.assertEqual(update.amplitudes["downlink"], 0.1)
        self.assertEqual(update.amplitudes["uplink"], 0.2)

    def test_maximum_amplitude_is_allowed(self):
        update = parse_noise_update(
            build_noise_update(
                1,
                {"downlink": 512.0},
                direction="downlink",
            )
        )
        self.assertEqual(update.amplitudes["downlink"], 512.0)

    def test_above_maximum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds maximum"):
            build_noise_update(
                1,
                {"downlink": 512.000001},
                direction="downlink",
            )

    def test_invalid_both_update_is_rejected_as_a_whole(self):
        with self.assertRaisesRegex(ValueError, "maximum"):
            build_noise_update(
                1,
                {"downlink": 0.2, "uplink": 513.0},
                direction="both",
            )

    def test_direction_keys_must_match(self):
        with self.assertRaisesRegex(ValueError, "exactly"):
            build_noise_update(
                1,
                {"downlink": 0.2, "uplink": 0.2},
                direction="downlink",
            )

    def test_non_finite_and_negative_rejected(self):
        for amplitude in (float("nan"), float("inf"), -0.1):
            with self.assertRaises(ValueError):
                build_noise_update(
                    1,
                    {"uplink": amplitude},
                    direction="uplink",
                )


if __name__ == "__main__":
    unittest.main()
