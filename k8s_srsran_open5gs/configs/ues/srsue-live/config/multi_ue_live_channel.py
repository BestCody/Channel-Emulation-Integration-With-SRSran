#!/usr/bin/env python3

import signal
import threading
from argparse import ArgumentParser

from gnuradio import blocks
from gnuradio import gr
from gnuradio import zeromq
from sionna_channel import sparse_channel_cc

from channel_control import ChannelControlServer
from fixed_channel import validate_sample_rate


class MultiUeLiveChannel(gr.top_block):
    def __init__(self, num_ues, sample_rate, control_bind):
        gr.top_block.__init__(self, "srsRAN live sparse channel")
        if num_ues < 1:
            raise ValueError("num_ues must be at least one")
        sample_rate = validate_sample_rate(sample_rate)

        identity_coefficients = (1.0 + 0.0j,)
        identity_delays = (0,)
        self.downlink_channel = sparse_channel_cc(
            identity_coefficients,
            identity_delays,
        )
        self.uplink_channel = sparse_channel_cc(
            identity_coefficients,
            identity_delays,
        )

        zmq_timeout = 100
        zmq_hwm = -1
        self.gnb_downlink_source = zeromq.req_source(
            gr.sizeof_gr_complex,
            1,
            "tcp://10.10.3.231:2000",
            zmq_timeout,
            False,
            zmq_hwm,
        )
        self.gnb_uplink_sink = zeromq.rep_sink(
            gr.sizeof_gr_complex,
            1,
            "tcp://10.10.3.232:2001",
            zmq_timeout,
            False,
            zmq_hwm,
        )
        self.throttle = blocks.throttle(
            gr.sizeof_gr_complex,
            sample_rate,
            True,
        )
        self.uplink_adder = blocks.add_vcc(1)

        self.ue_uplink_sources = []
        self.ue_downlink_sinks = []
        for index in range(num_ues):
            uplink_source = zeromq.req_source(
                gr.sizeof_gr_complex,
                1,
                f"tcp://10.10.3.232:{2101 + index}",
                zmq_timeout,
                False,
                zmq_hwm,
            )
            downlink_sink = zeromq.rep_sink(
                gr.sizeof_gr_complex,
                1,
                f"tcp://10.10.3.232:{2201 + index}",
                zmq_timeout,
                False,
                zmq_hwm,
            )
            self.ue_uplink_sources.append(uplink_source)
            self.ue_downlink_sinks.append(downlink_sink)
            self.connect((uplink_source, 0), (self.uplink_adder, index))
            self.connect((self.throttle, 0), (downlink_sink, 0))

        self.connect(
            (self.gnb_downlink_source, 0),
            (self.downlink_channel, 0),
            (self.throttle, 0),
        )
        self.connect(
            (self.uplink_adder, 0),
            (self.uplink_channel, 0),
            (self.gnb_uplink_sink, 0),
        )

        self.control_server = ChannelControlServer(
            bind_endpoint=control_bind,
            downlink=self.downlink_channel,
            uplink=self.uplink_channel,
            sample_rate=sample_rate,
        )
        print(
            "Stage 4 live sparse channel enabled with identity taps; "
            "no noise, movement, Sionna, or per-symbol channels",
            flush=True,
        )

    def start_live(self):
        self.start()
        self.control_server.start()

    def stop_live(self):
        self.control_server.stop()
        self.stop()
        self.wait()


def parse_args():
    parser = ArgumentParser(
        description="srsRAN live static sparse-channel flowgraph"
    )
    parser.add_argument("-n", "--num-ues", type=int, required=True)
    parser.add_argument("--sample-rate", type=float, required=True)
    parser.add_argument(
        "--control-bind",
        default="tcp://0.0.0.0:5555",
    )
    args = parser.parse_args()
    if args.num_ues < 1:
        parser.error("--num-ues must be at least one")
    try:
        validate_sample_rate(args.sample_rate)
    except ValueError as error:
        parser.error(str(error))
    return args


def main():
    args = parse_args()
    flowgraph = MultiUeLiveChannel(
        args.num_ues,
        args.sample_rate,
        args.control_bind,
    )
    stop_event = threading.Event()

    def request_stop(sig=None, frame=None):
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    flowgraph.start_live()
    try:
        stop_event.wait()
    finally:
        flowgraph.stop_live()


if __name__ == "__main__":
    main()
