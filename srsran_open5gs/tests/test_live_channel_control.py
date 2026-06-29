import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
sys.path.insert(0, str(CONFIG_DIR))

from channel_control import ChannelControlServer  # noqa: E402
from channel_protocol import MAX_DELAY  # noqa: E402
from channel_protocol import Tap  # noqa: E402
from channel_protocol import build_update  # noqa: E402
from channel_protocol import encode_message  # noqa: E402


class FakeBlock:
    def __init__(self, name, sample_count=10):
        self.name = name
        self._sample_count = sample_count
        self.channels = []
        self.fail = False

    def set_channel(self, coefficients, delays, noise_sigma):
        if self.fail:
            raise ValueError(f"{self.name} set_channel failed")
        self.channels.append((tuple(coefficients), tuple(delays), noise_sigma))

    def sample_count(self):
        return self._sample_count

    def update_count(self):
        return len(self.channels)

    def tap_count(self):
        return len(self.channels[-1][0]) if self.channels else 0


class LiveChannelControlTests(unittest.TestCase):
    def setUp(self):
        self.downlink = FakeBlock("downlink")
        self.uplink = FakeBlock("uplink")
        self.server = ChannelControlServer(
            "unused", self.downlink, self.uplink, 23_040_000
        )

    def stream(self, sequence=1, direction="both", noise_sigma=0.0):
        message = build_update(
            (Tap(0, 0.5 + 0j),), sequence, direction, noise_sigma=noise_sigma
        )
        return self.server._process_stream_frame(encode_message(message))

    def test_both_sets_both_blocks(self):
        self.assertTrue(self.stream())
        self.assertEqual(self.downlink.update_count(), 1)
        self.assertEqual(self.uplink.update_count(), 1)

    def test_downlink_only_sets_downlink(self):
        self.stream(direction="downlink")
        self.assertEqual(self.downlink.update_count(), 1)
        self.assertEqual(self.uplink.update_count(), 0)

    def test_uplink_only_sets_uplink(self):
        self.stream(direction="uplink")
        self.assertEqual(self.downlink.update_count(), 0)
        self.assertEqual(self.uplink.update_count(), 1)

    def test_latest_wins_applies_every_update(self):
        # streaming has no sequence gate: each CIR is applied
        self.stream(sequence=5)
        self.stream(sequence=5)
        self.assertEqual(self.downlink.update_count(), 2)

    def test_noise_sigma_reaches_set_channel(self):
        self.stream(noise_sigma=0.2)
        self.assertAlmostEqual(self.downlink.channels[-1][2], 0.2)
        self.assertAlmostEqual(self.uplink.channels[-1][2], 0.2)

    def test_invalid_frame_is_dropped_and_counted(self):
        bad = build_update((Tap(0, 0.5 + 0j),), 1)
        bad["taps"][0]["delay"] = MAX_DELAY + 1
        self.assertFalse(
            self.server._process_stream_frame(encode_message(bad))
        )
        self.assertEqual(self.server.rejected_updates, 1)
        self.assertEqual(self.downlink.update_count(), 0)

    def test_set_channel_failure_is_counted(self):
        self.uplink.fail = True
        self.assertFalse(self.stream())
        self.assertEqual(self.server.rejected_updates, 1)

    def test_config_and_status(self):
        config = self.server._handle_request(
            {"version": 1, "msg_type": "config_request"}
        )
        self.assertEqual(config["backend"], "dense-cir-stream")
        self.assertTrue(config["per_symbol_channels"])
        status = self.server._handle_request(
            {"version": 1, "msg_type": "status_request"}
        )
        self.assertEqual(status["downlink"]["update_count"], 0)

    def test_channel_update_not_accepted_on_control_socket(self):
        message = build_update((Tap(0, 0.5 + 0j),), 1)
        with self.assertRaises(ValueError):
            self.server._handle_request(message)


if __name__ == "__main__":
    unittest.main()
