import math
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
sys.path.insert(0, str(CONFIG_DIR))

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
            activate_at_sample=123456,
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

    def test_equal_delays_combine(self):
        taps = validate_taps(
            (Tap(4, 0.5 + 0.1j), Tap(4, 0.25 - 0.1j))
        )
        self.assertEqual(taps, (Tap(4, 0.75 + 0j),))

    def test_invalid_delays_and_tap_count_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_taps((Tap(256, 1 + 0j),))
        with self.assertRaises(ValueError):
            validate_taps(tuple(Tap(i, 0.01 + 0j) for i in range(49)))

    def test_nonfinite_and_boolean_values_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_taps((Tap(0, complex(math.inf, 0)),))
        message = build_update((Tap(0, 1 + 0j),), 1, 100)
        message["sequence"] = True
        with self.assertRaises(ValueError):
            parse_update(message)

    def test_all_zero_channel_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_taps((Tap(0, 1 + 0j), Tap(0, -1 + 0j)))

    def test_unknown_fields_are_rejected(self):
        message = build_update((Tap(0, 1 + 0j),), 1, 100)
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
