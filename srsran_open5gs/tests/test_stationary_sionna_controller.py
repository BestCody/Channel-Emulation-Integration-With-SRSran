import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "channel_emulation"))

from sionna_radio_config import RadioConfig  # noqa: E402
from stationary_sionna_controller import (  # noqa: E402
    report_sha256,
    validate_saved_report,
)


class StationarySionnaControllerTests(unittest.TestCase):
    def setUp(self):
        self.radio = RadioConfig(
            nr_arfcn=368500,
            band=3,
            carrier_hz=1_842_500_000.0,
            sample_rate=23_040_000.0,
        )
        self.report = {
            "carrier_hz": self.radio.carrier_hz,
            "sample_rate": self.radio.sample_rate,
            "conversion": {
                "safe_to_send": True,
                "errors": [],
                "normalization": "none",
                "absolute_coefficients_preserved": True,
                "retained_taps": [
                    {
                        "delay": 0,
                        "real": 0.1,
                        "imag": -0.2,
                        "power": 0.05,
                    }
                ],
            },
        }

    def test_valid_report_passes_live_channel_validation(self):
        taps = validate_saved_report(self.report, self.radio)
        self.assertEqual(len(taps), 1)
        self.assertEqual(taps[0].coefficient, 0.1 - 0.2j)

    def test_unsafe_reports_are_never_sent(self):
        mutations = (
            ("safe_to_send", False),
            ("normalization", "unit_energy"),
            ("absolute_coefficients_preserved", False),
            ("retained_taps", []),
        )
        for key, value in mutations:
            report = json.loads(json.dumps(self.report))
            report["conversion"][key] = value
            with self.assertRaises(ValueError):
                validate_saved_report(report, self.radio)

    def test_report_checksum_detects_changes(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as handle:
            handle.write('{"value":1}\n')
            path = pathlib.Path(handle.name)
        first = report_sha256(path)
        path.write_text('{"value":2}\n', encoding="utf-8")
        self.assertNotEqual(first, report_sha256(path))

    def test_live_client_uses_stream_endpoint(self):
        source = (
            ROOT / "channel_emulation/stationary_sionna_controller.py"
        ).read_text(encoding="utf-8")
        self.assertIn("--stream-endpoint", source)
        self.assertIn("stream_endpoint=args.stream_endpoint", source)

    def test_sionna_import_is_lazy(self):
        source = (
            ROOT / "channel_emulation/sionna_stationary.py"
        ).read_text(encoding="utf-8")
        prefix = source.split(
            "def calculate_stationary_channel",
            1,
        )[0]
        self.assertNotIn("import sionna", prefix)


if __name__ == "__main__":
    unittest.main()
