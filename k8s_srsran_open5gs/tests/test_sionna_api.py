import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "channel_emulation"))


try:
    import mitsuba as mi
    import sionna
    import sionna.rt
    SIONNA_AVAILABLE = True
except ImportError:
    SIONNA_AVAILABLE = False


@unittest.skipUnless(SIONNA_AVAILABLE, "Sionna environment required")
class SionnaApiTests(unittest.TestCase):
    def test_exact_api_and_minimal_scene(self):
        from sionna_stationary import (
            calculate_stationary_channel,
            load_scene_config,
        )

        self.assertEqual(sionna.__version__, "2.0.1")
        self.assertEqual(sionna.rt.__version__, "2.0.1")
        self.assertEqual(mi.variant(), "cuda_ad_mono_polarized")
        config = load_scene_config(
            ROOT
            / "channel_emulation/scenes/stationary_reflector.json"
        )
        report = calculate_stationary_channel(
            config,
            carrier_hz=1_842_500_000.0,
            sample_rate=23_040_000.0,
            repeats=1,
        )
        conversion = report["conversion"]
        self.assertTrue(conversion["original_paths"])
        self.assertTrue(conversion["safe_to_send"])
        self.assertTrue(conversion["retained_taps"])
        self.assertEqual(conversion["normalization"], "none")
        self.assertTrue(
            conversion["absolute_coefficients_preserved"]
        )


if __name__ == "__main__":
    unittest.main()
