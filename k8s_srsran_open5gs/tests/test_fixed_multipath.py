import importlib.util
import json
import math
import pathlib
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "ues" / "srsue-fixed" / "config"
MODULE_PATH = CONFIG_DIR / "fixed_channel.py"
NORMAL_TAPS_PATH = CONFIG_DIR / "fixed_three_path.json"
STRESS_TAPS_PATH = CONFIG_DIR / "fixed_three_path_stress.json"
FLOWGRAPH_PATH = CONFIG_DIR / "multi_ue_fixed_channel.py"
LAUNCHER_PATH = CONFIG_DIR / "start_gnu_multipath.sh"
SPEC = importlib.util.spec_from_file_location("fixed_channel", MODULE_PATH)
fixed_channel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixed_channel)

NORMAL_PROFILE = {
    "description": (
        "Verified 80% three-path profile for the current Atlas setup"
    ),
    "taps": [
        {"delay": 0, "real": 0.92, "imag": 0.0},
        {"delay": 12, "real": 0.176, "imag": 0.064},
        {"delay": 40, "real": 0.064, "imag": -0.096},
    ],
}
STRESS_PROFILE = {
    "description": (
        "Known synchronization-failure stress profile under the current "
        "Atlas setup; use only for controlled failure testing"
    ),
    "known_result": (
        "GNU Radio remains running, but the UE does not reach random access"
    ),
    "taps": [
        {"delay": 0, "real": 0.92, "imag": 0.0},
        {"delay": 12, "real": 0.22, "imag": 0.08},
        {"delay": 40, "real": 0.08, "imag": -0.12},
    ],
}


def apply_sparse_channel(samples, taps):
    output = [0.0j] * len(samples)
    for tap in taps:
        for index in range(tap.delay, len(samples)):
            output[index] += (
                complex(samples[index - tap.delay]) * tap.coefficient
            )
    return output


class FixedMultipathTests(unittest.TestCase):
    def write_taps(self, taps):
        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
        )
        self.addCleanup(pathlib.Path(temporary.name).unlink, missing_ok=True)
        json.dump({"taps": taps}, temporary)
        temporary.close()
        return temporary.name

    def test_normal_profile_is_exact(self):
        profile = json.loads(NORMAL_TAPS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(profile, NORMAL_PROFILE)
        taps = fixed_channel.load_taps_file(NORMAL_TAPS_PATH)
        self.assertEqual(
            taps,
            (
                fixed_channel.Tap(0, 0.92 + 0.0j),
                fixed_channel.Tap(12, 0.176 + 0.064j),
                fixed_channel.Tap(40, 0.064 - 0.096j),
            ),
        )

    def test_stress_profile_is_exact_and_labeled(self):
        profile = json.loads(STRESS_TAPS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(profile, STRESS_PROFILE)
        taps = fixed_channel.load_taps_file(STRESS_TAPS_PATH)
        self.assertEqual(
            taps,
            (
                fixed_channel.Tap(0, 0.92 + 0.0j),
                fixed_channel.Tap(12, 0.22 + 0.08j),
                fixed_channel.Tap(40, 0.08 - 0.12j),
            ),
        )

    def test_dense_fir_has_zeros_at_every_unused_delay(self):
        taps = fixed_channel.load_taps_file(NORMAL_TAPS_PATH)
        dense = fixed_channel.sparse_to_dense(taps)
        self.assertEqual(len(dense), 41)
        expected = {
            0: 0.92 + 0.0j,
            12: 0.176 + 0.064j,
            40: 0.064 - 0.096j,
        }
        for delay, coefficient in enumerate(dense):
            self.assertEqual(coefficient, expected.get(delay, 0.0j))

    def test_equal_delays_are_combined_by_complex_addition(self):
        path = self.write_taps(
            [
                {"delay": 12, "real": 0.22, "imag": 0.08},
                {"delay": 12, "real": -0.02, "imag": 0.01},
            ]
        )
        taps = fixed_channel.load_taps_file(path)
        self.assertEqual(len(taps), 1)
        self.assertEqual(taps[0].delay, 12)
        self.assertAlmostEqual(taps[0].coefficient.real, 0.20)
        self.assertAlmostEqual(taps[0].coefficient.imag, 0.09)

    def test_paths_that_cancel_completely_are_rejected(self):
        path = self.write_taps(
            [
                {"delay": 3, "real": 1.0, "imag": -0.5},
                {"delay": 3, "real": -1.0, "imag": 0.5},
            ]
        )
        with self.assertRaisesRegex(ValueError, "cannot all be zero"):
            fixed_channel.load_taps_file(path)

    def test_invalid_delays_are_rejected(self):
        invalid_delays = (-1, 1.5, 256, True)
        for delay in invalid_delays:
            with self.subTest(delay=delay):
                path = self.write_taps(
                    [{"delay": delay, "real": 1.0, "imag": 0.0}]
                )
                with self.assertRaises(ValueError):
                    fixed_channel.load_taps_file(path)

    def test_non_finite_coefficients_are_rejected(self):
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                path = self.write_taps(
                    [{"delay": 0, "real": value, "imag": 0.0}]
                )
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    fixed_channel.load_taps_file(path)

    def test_more_than_48_unique_taps_are_rejected(self):
        path = self.write_taps(
            [
                {"delay": delay, "real": 1.0, "imag": 0.0}
                for delay in range(49)
            ]
        )
        with self.assertRaisesRegex(ValueError, "at most 48"):
            fixed_channel.load_taps_file(path)

    def test_delayed_impulse_is_shifted_and_scaled(self):
        samples = [0.0j] * 12
        samples[2] = 1.0 + 0.0j
        output = apply_sparse_channel(
            samples,
            (fixed_channel.Tap(5, 0.5 - 0.25j),),
        )
        self.assertEqual(output[7], 0.5 - 0.25j)
        self.assertEqual(sum(value != 0.0j for value in output), 1)

    def test_multiple_paths_produce_expected_impulses(self):
        samples = [0.0j] * 48
        samples[0] = 1.0 + 0.0j
        taps = fixed_channel.load_taps_file(NORMAL_TAPS_PATH)
        output = apply_sparse_channel(samples, taps)
        self.assertEqual(output[0], 0.92 + 0.0j)
        self.assertEqual(output[12], 0.176 + 0.064j)
        self.assertEqual(output[40], 0.064 - 0.096j)
        self.assertEqual(sum(value != 0.0j for value in output), 3)

    def test_multipath_launcher_and_flowgraph_are_static_only(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        flowgraph = FLOWGRAPH_PATH.read_text(encoding="utf-8")
        self.assertIn("--taps-file", launcher)
        self.assertIn("load_taps_file", flowgraph)
        self.assertIn("sparse_to_dense", flowgraph)
        self.assertNotIn("channel_control", flowgraph)
        self.assertNotIn("sionna", flowgraph.lower())
        self.assertNotIn("noise", launcher.lower())


if __name__ == "__main__":
    unittest.main()
