import math
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
sys.path.insert(0, str(CONFIG_DIR))

from channel_protocol import MAX_DELAY  # noqa: E402
from channel_protocol import MAX_MESSAGE_BYTES  # noqa: E402
from channel_protocol import Tap  # noqa: E402
from channel_protocol import build_update  # noqa: E402
from channel_protocol import decode_message  # noqa: E402
from channel_protocol import encode_message  # noqa: E402
from channel_protocol import parse_update  # noqa: E402
from channel_protocol import validate_taps  # noqa: E402


class LiveChannelProtocolTests(unittest.TestCase):
    def test_exact_round_trip(self):
        message = build_update(
            (
                Tap(0, 0.92 + 0j),
                Tap(12, 0.176 + 0.064j),
                Tap(40, 0.064 - 0.096j),
            ),
            sequence=7,
        )
        self.assertEqual(
            parse_update(decode_message(encode_message(message))).taps,
            validate_taps(
                (
                    Tap(0, 0.92 + 0j),
                    Tap(12, 0.176 + 0.064j),
                    Tap(40, 0.064 - 0.096j),
                )
            ),
        )

    def test_dense_binary_frame_round_trip(self):
        taps = tuple(
            Tap(delay, complex(0.5 / (delay + 1), -0.25 / (delay + 1)))
            for delay in range(300)
        )
        message = build_update(taps, sequence=9)
        payload = encode_message(message)
        self.assertTrue(payload.startswith(b"SCIR"))  # binary frame
        self.assertEqual(len(payload), 36 + 300 * 20)  # header + 20 B/tap
        restored = parse_update(decode_message(payload))
        self.assertEqual(restored.taps, validate_taps(taps))
        self.assertEqual(restored.sequence, 9)
        self.assertEqual(restored.direction, "both")

    def test_noise_sigma_round_trips(self):
        message = build_update((Tap(0, 1 + 0j),), sequence=3, noise_sigma=0.125)
        restored = parse_update(decode_message(encode_message(message)))
        self.assertAlmostEqual(restored.noise_sigma, 0.125)
        # default is no noise
        plain = parse_update(decode_message(encode_message(
            build_update((Tap(0, 1 + 0j),), sequence=4)
        )))
        self.assertEqual(plain.noise_sigma, 0.0)

    def test_equal_delays_combine(self):
        taps = validate_taps(
            (Tap(4, 0.5 + 0.1j), Tap(4, 0.25 - 0.1j))
        )
        self.assertEqual(taps, (Tap(4, 0.75 + 0j),))

    def test_delay_bound_enforced_and_no_48_tap_cap(self):
        with self.assertRaises(ValueError):
            validate_taps((Tap(MAX_DELAY + 1, 1 + 0j),))
        # the old engine capped at 48 taps; dense CIRs keep them all
        many = validate_taps(tuple(Tap(i, 0.01 + 0j) for i in range(100)))
        self.assertEqual(len(many), 100)

    def test_nonfinite_and_boolean_values_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_taps((Tap(0, complex(math.inf, 0)),))
        message = build_update((Tap(0, 1 + 0j),), 1)
        message["sequence"] = True
        with self.assertRaises(ValueError):
            parse_update(message)

    def test_all_zero_channel_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_taps((Tap(0, 1 + 0j), Tap(0, -1 + 0j)))

    def test_unknown_fields_are_rejected(self):
        message = build_update((Tap(0, 1 + 0j),), 1)
        message["unexpected"] = 1
        with self.assertRaises(ValueError):
            parse_update(message)

    def test_oversized_and_nan_json_are_rejected(self):
        with self.assertRaises(ValueError):
            decode_message(b"{" + b" " * MAX_MESSAGE_BYTES + b"}")
        with self.assertRaises(ValueError):
            decode_message(b'{"value":NaN}')


if __name__ == "__main__":
    unittest.main()
