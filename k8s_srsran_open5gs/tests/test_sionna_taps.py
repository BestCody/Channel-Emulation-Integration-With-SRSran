import math
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "channel_emulation"))

from sionna_taps import convert_paths  # noqa: E402


class SionnaTapTests(unittest.TestCase):
    def test_rounding_and_equal_delay_combination(self):
        rate = 10.0
        result = convert_paths(
            [0.049, 0.051, 0.052],
            [1 + 0j, 2 + 1j, -1 + 2j],
            rate,
        )
        self.assertTrue(result["safe_to_send"])
        self.assertEqual(
            [(tap["delay"], tap["real"], tap["imag"])
             for tap in result["combined_taps"]],
            [(0, 1.0, 0.0), (1, 1.0, 3.0)],
        )

    def test_absolute_coefficients_are_preserved(self):
        result = convert_paths(
            [0.0, 1 / 23_040_000],
            [0.125 + 0.25j, -0.5j],
            23_040_000,
            normalization="none",
        )
        self.assertTrue(result["absolute_coefficients_preserved"])
        self.assertEqual(result["retained_taps"][0]["real"], 0.125)
        self.assertEqual(result["retained_taps"][1]["imag"], -0.5)

    def test_optional_normalization_is_separate(self):
        result = convert_paths(
            [0, 0.1],
            [3 + 0j, 4 + 0j],
            10,
            normalization="unit_energy",
        )
        self.assertFalse(result["absolute_coefficients_preserved"])
        self.assertAlmostEqual(result["retained_power"], 1.0)

    def test_late_path_rejects_entire_update(self):
        result = convert_paths(
            [256 / 10],
            [1 + 0j],
            10,
            late_policy="reject",
        )
        self.assertFalse(result["safe_to_send"])
        self.assertEqual(result["original_paths"][0]["status"], "late")

    def test_late_path_drop_is_explicit(self):
        result = convert_paths(
            [0, 256 / 10],
            [1 + 0j, 0.25 + 0j],
            10,
            late_policy="drop",
        )
        self.assertTrue(result["safe_to_send"])
        self.assertAlmostEqual(
            result["discarded_late_path_power"],
            0.0625,
        )

    def test_strongest_48_are_retained(self):
        result = convert_paths(
            [index / 100 for index in range(49)],
            [complex(index + 1, 0) for index in range(49)],
            100,
        )
        self.assertTrue(result["safe_to_send"])
        self.assertEqual(len(result["retained_taps"]), 48)
        self.assertNotIn(
            0,
            [tap["delay"] for tap in result["retained_taps"]],
        )
        self.assertGreater(result["discarded_power"], 0)

    def test_invalid_and_empty_channels_are_rejected(self):
        for delays, coefficients in (
            ([math.nan], [1 + 0j]),
            ([0], [complex(math.inf, 0)]),
            ([0], [0 + 0j]),
        ):
            result = convert_paths(delays, coefficients, 10)
            self.assertFalse(result["safe_to_send"])

    def test_sionna_padding_is_recorded_but_not_sent(self):
        result = convert_paths(
            [-1, 0],
            [0j, 1 + 0j],
            10,
        )
        self.assertTrue(result["safe_to_send"])
        self.assertEqual(
            result["original_paths"][0]["status"],
            "sionna_padding",
        )


if __name__ == "__main__":
    unittest.main()
