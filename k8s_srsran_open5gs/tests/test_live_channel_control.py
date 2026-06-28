import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
sys.path.insert(0, str(CONFIG_DIR))

from channel_control import ChannelControlServer  # noqa: E402
from channel_control import NO_PENDING_SEQUENCE  # noqa: E402
from channel_protocol import Tap  # noqa: E402
from channel_protocol import build_update  # noqa: E402


class Prepared:
    def __init__(self, owner, sequence, activation):
        self.owner = owner
        self.sequence = sequence
        self.activation = activation


class FakeBlock:
    def __init__(self, name, sample_count=10):
        self.name = name
        self._sample_count = sample_count
        self._active = 0
        self._pending = NO_PENDING_SEQUENCE
        self._requested = 0
        self._actual = 0
        self._activation_time = 0
        self._latest = 0
        self.fail_prepare = False
        self.single_commits = 0

    def prepare_channel(self, sequence, activation, coefficients, delays):
        if self.fail_prepare:
            raise ValueError(f"{self.name} preparation failed")
        if len(coefficients) != len(delays):
            raise ValueError("mismatch")
        return Prepared(self, sequence, activation)

    def commit_channel(self, prepared):
        self.single_commits += 1
        if prepared.owner is not self:
            return False
        self._pending = prepared.sequence
        self._requested = prepared.activation
        self._latest = prepared.sequence
        return True

    def sample_count(self):
        return self._sample_count

    def active_sequence(self):
        return self._active

    def pending_sequence(self):
        return self._pending

    def requested_activation_sample(self):
        return self._requested

    def actual_activation_sample(self):
        return self._actual

    def activation_time_ns(self):
        return self._activation_time

    def latest_received_sequence(self):
        return self._latest


class FakeCommitBoth:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def __call__(self, downlink, down_prepared, uplink, up_prepared):
        self.calls += 1
        if self.fail:
            return False
        if down_prepared.owner is not downlink:
            return False
        if up_prepared.owner is not uplink:
            return False
        if down_prepared.sequence != up_prepared.sequence:
            return False
        downlink._pending = down_prepared.sequence
        uplink._pending = up_prepared.sequence
        downlink._requested = down_prepared.activation
        uplink._requested = up_prepared.activation
        downlink._latest = down_prepared.sequence
        uplink._latest = up_prepared.sequence
        return True


class LiveChannelControlTests(unittest.TestCase):
    def setUp(self):
        self.downlink = FakeBlock("downlink")
        self.uplink = FakeBlock("uplink")
        self.commit_both = FakeCommitBoth()
        self.server = ChannelControlServer(
            "unused",
            self.downlink,
            self.uplink,
            23_040_000,
            commit_both_function=self.commit_both,
        )

    def message(self, sequence=1, activation=100, direction="both"):
        return build_update(
            (Tap(0, 0.5 + 0j),),
            sequence,
            activation,
            direction,
        )

    def test_both_uses_one_transaction_call(self):
        response = self.server._handle_request(self.message())
        self.assertEqual(response["msg_type"], "channel_ack")
        self.assertEqual(self.commit_both.calls, 1)
        self.assertEqual(self.downlink.single_commits, 0)
        self.assertEqual(self.uplink.single_commits, 0)
        self.assertEqual(self.downlink.pending_sequence(), 1)
        self.assertEqual(self.uplink.pending_sequence(), 1)

    def test_second_preparation_failure_changes_neither_block(self):
        self.uplink.fail_prepare = True
        with self.assertRaisesRegex(ValueError, "uplink preparation failed"):
            self.server._handle_request(self.message())
        self.assertEqual(self.commit_both.calls, 0)
        self.assertEqual(self.downlink.pending_sequence(), NO_PENDING_SEQUENCE)
        self.assertEqual(self.uplink.pending_sequence(), NO_PENDING_SEQUENCE)

    def test_transaction_failure_changes_neither_block(self):
        self.commit_both.fail = True
        with self.assertRaisesRegex(RuntimeError, "transaction commit failed"):
            self.server._handle_request(self.message())
        self.assertEqual(self.downlink.pending_sequence(), NO_PENDING_SEQUENCE)
        self.assertEqual(self.uplink.pending_sequence(), NO_PENDING_SEQUENCE)

    def test_stale_sequence_is_rejected(self):
        self.server._handle_request(self.message(sequence=2))
        with self.assertRaisesRegex(ValueError, "not newer"):
            self.server._handle_request(self.message(sequence=2, activation=200))

    def test_past_activation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "older than current sample"):
            self.server._handle_request(self.message(activation=9))

    def test_single_direction_uses_single_commit(self):
        self.server._handle_request(self.message(direction="downlink"))
        self.assertEqual(self.commit_both.calls, 0)
        self.assertEqual(self.downlink.single_commits, 1)
        self.assertEqual(self.uplink.single_commits, 0)

    def test_config_and_status(self):
        config = self.server._handle_request(
            {"version": 1, "msg_type": "config_request"}
        )
        self.assertEqual(config["backend"], "sparse-cpp-live")
        self.assertFalse(config["per_symbol_channels"])
        status = self.server._handle_request(
            {"version": 1, "msg_type": "status_request"}
        )
        self.assertEqual(status["downlink"]["active_sequence"], 0)


if __name__ == "__main__":
    unittest.main()
