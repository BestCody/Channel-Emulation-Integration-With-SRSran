#!/usr/bin/env python3

import unittest

from gnuradio import blocks
from gnuradio import gr
from sionna_channel import commit_both
from sionna_channel import sparse_channel_cc


def run_channel(samples, coefficients, delays):
    flowgraph = gr.top_block()
    source = blocks.vector_source_c(samples, False)
    channel = sparse_channel_cc(coefficients, delays)
    sink = blocks.vector_sink_c()
    flowgraph.connect(source, channel, sink)
    flowgraph.run()
    return tuple(sink.data())


class SparseChannelImportTests(unittest.TestCase):
    def test_identity(self):
        samples = (1 + 2j, -3 + 4j)
        self.assertEqual(run_channel(samples, (1 + 0j,), (0,)), samples)

    def test_delayed_paths(self):
        samples = [0j] * 48
        samples[0] = 1 + 0j
        output = run_channel(
            samples,
            (0.92 + 0j, 0.176 + 0.064j, 0.064 - 0.096j),
            (0, 12, 40),
        )
        self.assertAlmostEqual(output[0], 0.92 + 0j)
        self.assertAlmostEqual(output[12], 0.176 + 0.064j)
        self.assertAlmostEqual(output[40], 0.064 - 0.096j)

    def test_limits(self):
        block = sparse_channel_cc(
            tuple(0.01 + 0j for _ in range(48)),
            tuple(range(48)),
        )
        self.assertIsNotNone(block)
        with self.assertRaises(RuntimeError):
            sparse_channel_cc(
                tuple(0.01 + 0j for _ in range(49)),
                tuple(range(49)),
            )

    def test_commit_both_is_one_binding_call(self):
        downlink = sparse_channel_cc((1 + 0j,), (0,))
        uplink = sparse_channel_cc((1 + 0j,), (0,))
        downlink_prepared = downlink.prepare_channel(
            1, 1000, (0.5 + 0j,), (0,)
        )
        uplink_prepared = uplink.prepare_channel(
            1, 1000, (0.5 + 0j,), (0,)
        )
        self.assertTrue(
            commit_both(
                downlink,
                downlink_prepared,
                uplink,
                uplink_prepared,
            )
        )
        self.assertEqual(downlink.pending_sequence(), 1)
        self.assertEqual(uplink.pending_sequence(), 1)

    def test_commit_both_rejects_wrong_owner_without_publication(self):
        downlink = sparse_channel_cc((1 + 0j,), (0,))
        uplink = sparse_channel_cc((1 + 0j,), (0,))
        downlink_prepared = downlink.prepare_channel(
            1, 1000, (0.5 + 0j,), (0,)
        )
        uplink_prepared = uplink.prepare_channel(
            1, 1000, (0.5 + 0j,), (0,)
        )
        self.assertFalse(
            commit_both(
                downlink,
                uplink_prepared,
                uplink,
                downlink_prepared,
            )
        )
        none_pending = (1 << 64) - 1
        self.assertEqual(downlink.pending_sequence(), none_pending)
        self.assertEqual(uplink.pending_sequence(), none_pending)


if __name__ == "__main__":
    unittest.main(verbosity=2)
