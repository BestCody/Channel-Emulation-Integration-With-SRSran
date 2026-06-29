import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "configs/ues/srsue-live/config"
NOISE_CONFIG = REPO_ROOT / "configs/ues/srsue-noise/config"
sys.path.insert(0, str(LIVE_CONFIG))
sys.path.insert(0, str(NOISE_CONFIG))

from channel_protocol import Tap  # noqa: E402
from channel_protocol import build_update  # noqa: E402
from channel_protocol import encode_message  # noqa: E402
from noise_channel_control import NoiseChannelControlServer  # noqa: E402


class FakeBlock:
    def __init__(self):
        self._sample = 10
        self.channels = []

    def set_channel(self, coefficients, delays, noise_sigma):
        self.channels.append((tuple(coefficients), tuple(delays), noise_sigma))

    def sample_count(self):
        return self._sample

    def update_count(self):
        return len(self.channels)

    def tap_count(self):
        return len(self.channels[-1][0]) if self.channels else 0


class NoiseControlTests(unittest.TestCase):
    def setUp(self):
        self.downlink = FakeBlock()
        self.uplink = FakeBlock()
        self.server = NoiseChannelControlServer(
            "unused",
            self.downlink,
            self.uplink,
            23_040_000,
            {
                "downlink_signal": lambda: 0.04,
                "uplink_signal": lambda: 0.01,
            },
        )

    def channel(self, sequence, direction, sigma):
        return encode_message(
            build_update(
                (Tap(0, 1 + 0j),), sequence, direction, noise_sigma=sigma
            )
        )

    def test_status_reports_signal_power(self):
        status = self.server._handle_request(
            {"version": 1, "msg_type": "status_request"}
        )
        self.assertEqual(status["signal"]["downlink"], 0.04)
        self.assertEqual(status["signal"]["uplink"], 0.01)

    def test_config_marks_noise_carried_in_cir(self):
        config = self.server._handle_request(
            {"version": 1, "msg_type": "config_request"}
        )
        self.assertTrue(config["noise_control"]["enabled"])
        self.assertEqual(config["noise_control"]["carried_in"], "cir_stream")

    def test_sigma_applied_to_both_blocks(self):
        self.assertTrue(
            self.server._process_stream_frame(
                self.channel(1, "both", 0.2)
            )
        )
        self.assertAlmostEqual(self.downlink.channels[-1][2], 0.2)
        self.assertAlmostEqual(self.uplink.channels[-1][2], 0.2)

    def test_per_direction_sigma(self):
        self.server._process_stream_frame(self.channel(1, "downlink", 0.1))
        self.server._process_stream_frame(self.channel(2, "uplink", 0.05))
        self.assertAlmostEqual(self.downlink.channels[-1][2], 0.1)
        self.assertAlmostEqual(self.uplink.channels[-1][2], 0.05)
        self.assertEqual(self.downlink.update_count(), 1)
        self.assertEqual(self.uplink.update_count(), 1)


if __name__ == "__main__":
    unittest.main()
