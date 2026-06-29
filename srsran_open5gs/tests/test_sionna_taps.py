import math
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "channel_emulation"))

from sionna_taps import Tap  # noqa: E402
from sionna_taps import convert_paths  # noqa: E402
from sionna_taps import interpolate_taps  # noqa: E402


def _taps(result):
    return {
        tap["delay"]: complex(tap["real"], tap["imag"])
        for tap in result["retained_taps"]
    }


class SionnaTapTests(unittest.TestCase):
    def test_integer_delay_is_a_single_unit_tap(self):
        rate = 10.0
        result = convert_paths([2 / rate], [1 + 0j], rate)
        self.assertTrue(result["safe_to_send"])
        taps = _taps(result)
        self.assertAlmostEqual(taps[2].real, 1.0, places=9)
        self.assertAlmostEqual(taps[2].imag, 0.0, places=9)
        for delay, coeff in taps.items():
            if delay != 2:
                self.assertAlmostEqual(abs(coeff), 0.0, places=9)

    def test_fractional_delay_spreads_and_preserves_energy(self):
        rate = 10.0
        # Kernel sits inside the buffer
        result = convert_paths([20.5 / rate], [1 + 0j], rate)
        self.assertTrue(result["safe_to_send"])
        taps = _taps(result)
        self.assertGreater(len(taps), 2)  # a fractional delay spreads
        energy = sum(abs(coeff) ** 2 for coeff in taps.values())
        self.assertAlmostEqual(energy, 1.0, places=9)
        # Main lobe straddles samples 20 and 21
        powers = {delay: abs(coeff) ** 2 for delay, coeff in taps.items()}
        self.assertAlmostEqual(powers[20], powers[21], places=9)

    def test_coincident_paths_combine_coherently(self):
        rate = 10.0
        result = convert_paths([3 / rate, 3 / rate], [1 + 0j, 0 + 1j], rate)
        taps = _taps(result)
        self.assertAlmostEqual(taps[3].real, 1.0, places=9)
        self.assertAlmostEqual(taps[3].imag, 1.0, places=9)

    def test_absolute_power_is_preserved_per_path(self):
        rate = 23_040_000.0
        first = 0.125 + 0.25j
        second = -0.5j
        # Non-overlapping paths add energy independently
        result = convert_paths(
            [20.25 / rate, 40.5 / rate],
            [first, second],
            rate,
            normalization="none",
        )
        self.assertTrue(result["absolute_coefficients_preserved"])
        self.assertAlmostEqual(
            result["retained_power"],
            abs(first) ** 2 + abs(second) ** 2,
            places=9,
        )

    def test_optional_unit_energy_normalization(self):
        rate = 10.0
        result = convert_paths(
            [20.5 / rate, 40.5 / rate],
            [3 + 0j, 4 + 0j],
            rate,
            normalization="unit_energy",
        )
        self.assertFalse(result["absolute_coefficients_preserved"])
        self.assertAlmostEqual(result["retained_power"], 1.0, places=9)

    def test_no_tap_cap_keeps_all_paths(self):
        rate = 100.0
        # The engine keeps all distinct sample delays
        delays = [(2 * index) / rate for index in range(100)]
        coefficients = [complex(index + 1, 0) for index in range(100)]
        result = convert_paths(delays, coefficients, rate)
        self.assertTrue(result["safe_to_send"])
        taps = _taps(result)
        self.assertEqual(len(taps), 100)
        # The weakest path is retained
        self.assertIn(0, taps)
        self.assertAlmostEqual(taps[0].real, 1.0, places=9)

    def test_path_beyond_channel_length_rejects_update(self):
        rate = 10.0
        result = convert_paths(
            [1500 / rate],  # 1500 samples > default 1024 channel length
            [1 + 0j],
            rate,
            late_policy="reject",
        )
        self.assertFalse(result["safe_to_send"])
        self.assertEqual(result["original_paths"][0]["status"], "late")

    def test_path_beyond_channel_length_drop_is_explicit(self):
        rate = 10.0
        result = convert_paths(
            [0.0, 1500 / rate],
            [1 + 0j, 0.25 + 0j],
            rate,
            late_policy="drop",
        )
        self.assertTrue(result["safe_to_send"])
        self.assertAlmostEqual(
            result["discarded_late_path_power"], 0.0625, places=9
        )

    def test_channel_length_is_configurable(self):
        rate = 10.0
        result = convert_paths(
            [300 / rate],  # 300 samples > 256
            [1 + 0j],
            rate,
            max_channel_len=256,
            late_policy="reject",
        )
        self.assertFalse(result["safe_to_send"])

    def test_invalid_and_empty_channels_are_rejected(self):
        for delays, coefficients in (
            ([math.nan], [1 + 0j]),
            ([0], [complex(math.inf, 0)]),
            ([0], [0 + 0j]),
        ):
            result = convert_paths(delays, coefficients, 10)
            self.assertFalse(result["safe_to_send"])

    def test_sionna_padding_is_recorded_but_not_sent(self):
        result = convert_paths([-1, 0], [0j, 1 + 0j], 10)
        self.assertTrue(result["safe_to_send"])
        self.assertEqual(
            result["original_paths"][0]["status"], "sionna_padding"
        )
        taps = _taps(result)
        self.assertAlmostEqual(taps[0].real, 1.0, places=9)


class InterpolateTapsTests(unittest.TestCase):
    def setUp(self):
        self.a = (Tap(0, 1 + 0j), Tap(4, 0.5 + 0j))
        self.b = (Tap(0, 0 + 0j), Tap(4, 1.5 + 0j))

    def test_alpha_zero_returns_first(self):
        result = {t.delay: t.coefficient for t in interpolate_taps(self.a, self.b, 0.0)}
        self.assertEqual(result[0], 1 + 0j)
        self.assertEqual(result[4], 0.5 + 0j)

    def test_alpha_one_returns_second(self):
        result = {t.delay: t.coefficient for t in interpolate_taps(self.a, self.b, 1.0)}
        self.assertEqual(result[4], 1.5 + 0j)
        self.assertNotIn(0, result)  # zero coefficient is dropped

    def test_alpha_half_is_midpoint(self):
        result = {t.delay: t.coefficient for t in interpolate_taps(self.a, self.b, 0.5)}
        self.assertAlmostEqual(result[0], 0.5 + 0j)
        self.assertAlmostEqual(result[4], 1.0 + 0j)

    def test_disjoint_delays_are_scaled(self):
        a = (Tap(0, 1 + 0j),)
        b = (Tap(7, 1 + 0j),)
        result = {t.delay: t.coefficient for t in interpolate_taps(a, b, 0.25)}
        self.assertAlmostEqual(result[0], 0.75 + 0j)
        self.assertAlmostEqual(result[7], 0.25 + 0j)

    def test_alpha_out_of_range_is_rejected(self):
        with self.assertRaises(ValueError):
            interpolate_taps(self.a, self.b, 1.5)


if __name__ == "__main__":
    unittest.main()
