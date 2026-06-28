import importlib.util
import math
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT
    / "configs"
    / "ues"
    / "srsue-fixed"
    / "config"
    / "fixed_channel.py"
)
FLOWGRAPH_PATH = MODULE_PATH.with_name("multi_ue_fixed_channel.py")
LAUNCHER_PATH = MODULE_PATH.with_name("start_gnu_fixed_channel.sh")
SPEC = importlib.util.spec_from_file_location("fixed_channel", MODULE_PATH)
fixed_channel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixed_channel)


class FixedChannelTests(unittest.TestCase):
    def test_zero_db_preserves_amplitude(self):
        self.assertEqual(fixed_channel.db_to_amplitude(0), 1.0)

    def test_six_db_is_about_half_amplitude(self):
        self.assertAlmostEqual(
            fixed_channel.db_to_amplitude(6.0),
            0.5011872336272722,
            places=12,
        )

    def test_fixed_channel_is_one_zero_delay_real_tap(self):
        taps = fixed_channel.fixed_attenuation_taps(6.0)
        self.assertEqual(len(taps), 1)
        self.assertEqual(taps[0].delay, 0)
        self.assertAlmostEqual(taps[0].coefficient.real, 0.5011872336272722)
        self.assertEqual(taps[0].coefficient.imag, 0.0)

    def test_complex_samples_are_scaled(self):
        amplitude = fixed_channel.db_to_amplitude(6.0)
        output = fixed_channel.scale_samples(
            (1 + 2j, -3 + 4j),
            attenuation_db=6.0,
        )
        self.assertAlmostEqual(output[0].real, amplitude)
        self.assertAlmostEqual(output[0].imag, 2 * amplitude)
        self.assertAlmostEqual(output[1].real, -3 * amplitude)
        self.assertAlmostEqual(output[1].imag, 4 * amplitude)

    def test_negative_attenuation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            fixed_channel.db_to_amplitude(-0.1)

    def test_non_finite_attenuation_is_rejected(self):
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    fixed_channel.db_to_amplitude(value)

    def test_invalid_sample_rates_are_rejected(self):
        for value in (0, -1, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    fixed_channel.validate_sample_rate(value)

    def test_sample_rate_is_read_from_ue_configuration(self):
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
        ) as config:
            config.write("[rf]\nsrate = 15.36e6\n")
            config.flush()
            self.assertEqual(
                fixed_channel.sample_rate_from_ue_config(config.name),
                15_360_000.0,
            )

    def test_sample_rate_cli_uses_the_same_parser(self):
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
        ) as config:
            config.write("[other]\nsrate = 1\n[rf]\nsrate = 15.36e6\n")
            config.flush()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "sample-rate",
                    config.name,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.stdout.strip(), "15360000")

    def test_launcher_uses_the_tested_sample_rate_cli(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        self.assertIn("fixed_channel.py sample-rate", launcher)
        self.assertNotIn("awk", launcher)

    def test_flowgraph_waits_without_reading_stdin(self):
        flowgraph = FLOWGRAPH_PATH.read_text(encoding="utf-8")
        self.assertIn("stop_event = threading.Event()", flowgraph)
        self.assertIn("stop_event.wait()", flowgraph)
        self.assertIn("signal.SIGINT, request_stop", flowgraph)
        self.assertIn("signal.SIGTERM, request_stop", flowgraph)
        self.assertNotIn("input(", flowgraph)

    def test_current_radio_sample_rates_are_consistent(self):
        sources = (
            (
                "gNB base_srate",
                REPO_ROOT
                / "configs/srsRAN/srsran-gnb/config/srsran-gnb.yaml",
                r"base_srate=([0-9.]+e[0-9]+)",
                1.0,
            ),
            (
                "gNB srate",
                REPO_ROOT
                / "configs/srsRAN/srsran-gnb/config/srsran-gnb.yaml",
                r"^\s*srate:\s*([0-9.]+)",
                1_000_000.0,
            ),
            (
                "UE srate",
                REPO_ROOT / "configs/ues/srsue/config/ue0.conf",
                r"^\s*srate\s*=\s*([0-9.]+e[0-9]+)",
                1.0,
            ),
            (
                "UE base_srate",
                REPO_ROOT / "configs/ues/srsue/config/ue0.conf",
                r"base_srate=([0-9.]+e[0-9]+)",
                1.0,
            ),
            (
                "GNU Radio samp_rate",
                REPO_ROOT
                / "configs/ues/srsue/config/multi_ue_scenario.py",
                r"^\s*samp_rate\s*=\s*([0-9]+)",
                1.0,
            ),
        )

        detected = {}
        for label, path, pattern, multiplier in sources:
            match = re.search(
                pattern,
                path.read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )
            self.assertIsNotNone(match, f"sample rate not found for {label}")
            detected[label] = float(match.group(1)) * multiplier

        self.assertEqual(
            len(set(detected.values())),
            1,
            f"inconsistent configured sample rates: {detected}",
        )


if __name__ == "__main__":
    unittest.main()
