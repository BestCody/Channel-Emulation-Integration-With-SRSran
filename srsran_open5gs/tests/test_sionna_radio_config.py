import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "channel_emulation"))

from sionna_radio_config import (  # noqa: E402
    gnb_radio_config,
    nr_arfcn_to_hz,
    ue_sample_rate,
)


class SionnaRadioConfigTests(unittest.TestCase):
    def test_current_nr_arfcn_frequency(self):
        self.assertEqual(nr_arfcn_to_hz(368500), 1_842_500_000.0)

    def test_global_raster_segments(self):
        self.assertEqual(nr_arfcn_to_hz(0), 0.0)
        self.assertEqual(nr_arfcn_to_hz(600000), 3_000_000_000.0)
        self.assertEqual(
            nr_arfcn_to_hz(2016667),
            24_250_080_000.0,
        )

    def test_current_configs(self):
        arfcn, band, carrier = gnb_radio_config(
            ROOT / "configs/srsRAN/srsran-gnb/config/srsran-gnb.yaml"
        )
        self.assertEqual((arfcn, band), (368500, 3))
        self.assertEqual(carrier, 1_842_500_000.0)
        self.assertEqual(
            ue_sample_rate(ROOT / "configs/ues/srsue/config/ue0.conf"),
            23_040_000.0,
        )

    def test_invalid_values(self):
        for value in (-1, 3279166, True, 1.2):
            with self.assertRaises(ValueError):
                nr_arfcn_to_hz(value)


if __name__ == "__main__":
    unittest.main()
