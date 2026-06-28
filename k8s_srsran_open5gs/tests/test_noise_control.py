import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "configs/ues/srsue-live/config"
NOISE_CONFIG = REPO_ROOT / "configs/ues/srsue-noise/config"
sys.path.insert(0, str(LIVE_CONFIG))
sys.path.insert(0, str(NOISE_CONFIG))

from channel_control import NO_PENDING_SEQUENCE  # noqa: E402
from channel_protocol import Tap  # noqa: E402
from channel_protocol import build_update  # noqa: E402
from noise_channel_control import NoiseChannelControlServer  # noqa: E402
from noise_protocol import build_noise_update  # noqa: E402


class Prepared:
    def __init__(self, owner, sequence, activation):
        self.owner = owner
        self.sequence = sequence
        self.activation = activation


class FakeBlock:
    def __init__(self):
        self._sample = 10
        self._latest = 0
        self._pending = NO_PENDING_SEQUENCE

    def prepare_channel(self, sequence, activation, coefficients, delays):
        return Prepared(self, sequence, activation)

    def commit_channel(self, prepared):
        self._latest = prepared.sequence
        self._pending = prepared.sequence
        return True

    def sample_count(self):
        return self._sample

    def active_sequence(self):
        return 0

    def pending_sequence(self):
        return self._pending

    def requested_activation_sample(self):
        return 0

    def actual_activation_sample(self):
        return 0

    def activation_time_ns(self):
        return 0

    def latest_received_sequence(self):
        return self._latest


def fake_commit_both(downlink, down_prepared, uplink, up_prepared):
    if down_prepared.owner is not downlink:
        return False
    if up_prepared.owner is not uplink:
        return False
    downlink._latest = down_prepared.sequence
    uplink._latest = up_prepared.sequence
    downlink._pending = down_prepared.sequence
    uplink._pending = up_prepared.sequence
    return True


class FakeNoise:
    def __init__(self):
        self.amplitude = 0.0
        self.calls = 0
        self.fail = False

    def set_amplitude(self, amplitude):
        self.calls += 1
        if self.fail:
            raise RuntimeError("setter failed")
        self.amplitude = amplitude


class NoiseControlTests(unittest.TestCase):
    def setUp(self):
        self.downlink = FakeBlock()
        self.uplink = FakeBlock()
        self.downlink_noise = FakeNoise()
        self.uplink_noise = FakeNoise()
        self.server = NoiseChannelControlServer(
            "unused",
            self.downlink,
            self.uplink,
            23_040_000,
            self.downlink_noise,
            self.uplink_noise,
            {
                "downlink_signal": lambda: 0.04,
                "uplink_signal": lambda: 0.01,
                "downlink_noise": lambda: 0.0004,
                "uplink_noise": lambda: 0.0001,
            },
            commit_both_function=fake_commit_both,
        )

    def noise_message(self, sequence=1, down=0.1, up=0.2):
        return build_noise_update(
            sequence,
            {"downlink": down, "uplink": up},
            direction="both",
        )

    def test_noise_update_uses_existing_server_handler(self):
        response = self.server._handle_request(self.noise_message())
        self.assertEqual(response["msg_type"], "noise_ack")
        self.assertEqual(self.downlink_noise.amplitude, 0.1)
        self.assertEqual(self.uplink_noise.amplitude, 0.2)
        self.assertEqual(self.server.last_accepted_noise_sequence, 1)

    def test_channel_and_noise_sequences_are_independent(self):
        self.server._handle_request(self.noise_message(sequence=7))
        channel = build_update(
            (Tap(0, 0.5 + 0j),),
            sequence=1,
            activate_at_sample=100,
            direction="both",
        )
        response = self.server._handle_request(channel)
        self.assertEqual(response["msg_type"], "channel_ack")
        self.assertEqual(self.server.last_accepted_sequence, 1)
        self.assertEqual(self.server.last_accepted_noise_sequence, 7)

    def test_stale_noise_sequence_changes_neither_source(self):
        self.server._handle_request(self.noise_message(sequence=2))
        with self.assertRaisesRegex(ValueError, "not newer"):
            self.server._handle_request(
                self.noise_message(sequence=2, down=0.3, up=0.4)
            )
        self.assertEqual(self.downlink_noise.amplitude, 0.1)
        self.assertEqual(self.uplink_noise.amplitude, 0.2)

    def test_second_setter_failure_rolls_back_first(self):
        self.server._handle_request(self.noise_message(sequence=1))
        self.uplink_noise.fail = True
        with self.assertRaisesRegex(RuntimeError, "setter failed"):
            self.server._handle_request(
                self.noise_message(sequence=2, down=0.3, up=0.4)
            )
        self.assertEqual(self.downlink_noise.amplitude, 0.1)
        self.assertEqual(self.server.last_accepted_noise_sequence, 1)

    def test_above_maximum_never_calls_setters(self):
        message = self.noise_message()
        message["amplitudes"]["uplink"] = 512.1
        with self.assertRaisesRegex(ValueError, "maximum"):
            self.server._handle_request(message)
        self.assertEqual(self.downlink_noise.calls, 0)
        self.assertEqual(self.uplink_noise.calls, 0)

    def test_status_contains_pre_noise_signal_power(self):
        status = self.server._handle_request(
            {"version": 1, "msg_type": "status_request"}
        )
        self.assertEqual(status["noise"]["downlink"]["signal_power"], 0.04)
        self.assertEqual(status["noise"]["uplink"]["signal_power"], 0.01)
        config = self.server._handle_request(
            {"version": 1, "msg_type": "config_request"}
        )
        self.assertEqual(
            config["noise_control"]["maximum_amplitude"],
            512.0,
        )


if __name__ == "__main__":
    unittest.main()
