import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "configs/ues/srsue-live/config"))

from fixed_channel import samples_per_symbol  # noqa: E402


class SymbolTimingTests(unittest.TestCase):
    def test_15khz_at_23p04_msps(self):
        self.assertEqual(samples_per_symbol(23_040_000, 15.0), 1646)

    def test_30khz_halves_symbol_length(self):
        self.assertEqual(samples_per_symbol(23_040_000, 30.0), 823)

    def test_default_is_15khz(self):
        self.assertEqual(
            samples_per_symbol(23_040_000),
            samples_per_symbol(23_040_000, 15.0),
        )

    def test_rejects_subcarrier_below_15khz(self):
        with self.assertRaises(ValueError):
            samples_per_symbol(23_040_000, 7.5)


if __name__ == "__main__":
    unittest.main()
