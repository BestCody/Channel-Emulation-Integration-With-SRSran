#!/usr/bin/env python3

import unittest

from gnuradio import blocks
from gnuradio import gr
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
            tuple(0.01 + 0j for _ in range(1024)),
            tuple(range(1024)),
        )
        self.assertIsNotNone(block)
        with self.assertRaises(RuntimeError):
            sparse_channel_cc(
                tuple(0.01 + 0j for _ in range(1025)),
                tuple(range(1025)),
            )

    def test_set_channel_updates_current(self):
        block = sparse_channel_cc((1 + 0j,), (0,))
        self.assertEqual(block.update_count(), 0)
        block.set_channel((0.5 + 0j, 0.25 + 0j), (0, 4), 0.0)
        self.assertEqual(block.update_count(), 1)
        self.assertEqual(block.tap_count(), 2)

    def test_set_channel_carries_noise_sigma(self):
        block = sparse_channel_cc((1 + 0j,), (0,))
        block.set_channel((1 + 0j,), (0,), 0.25)
        self.assertAlmostEqual(block.noise_sigma(), 0.25)

    def test_set_channel_rejects_invalid(self):
        block = sparse_channel_cc((1 + 0j,), (0,))
        with self.assertRaises(RuntimeError):
            block.set_channel((1 + 0j,), (2000,), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
